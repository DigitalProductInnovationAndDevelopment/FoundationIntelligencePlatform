from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Iterable

import requests


BASE_URL = "https://ccewuksprdoneregsadata1.blob.core.windows.net/data/json"
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1] / "data" / "raw" / "charity_commission_bulk"
)

# Deliberately excludes the trustee extract. It is not needed for funder enrichment
# and would add unnecessary personal data to the local cache.
DATASETS = (
    "charity",
    "charity_annual_return_history",
    "charity_annual_return_parta",
    "charity_annual_return_partb",
    "charity_area_of_operation",
    "charity_classification",
    "charity_event_history",
    "charity_governing_document",
    "charity_other_names",
    "charity_other_regulators",
    "charity_policy",
    "charity_published_report",
)


def dataset_url(dataset: str) -> str:
    return f"{BASE_URL}/publicextract.{dataset}.zip"


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: zipfile.ZipFile, destination: Path) -> list[str]:
    destination_root = destination.resolve()
    extracted: list[str] = []
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        if destination_root != member_path and destination_root not in member_path.parents:
            raise ValueError(f"Unsafe path in archive: {member.filename}")
        extracted.append(member.filename)
    archive.extractall(destination)
    return extracted


def download_archive(
    dataset: str,
    output_dir: Path,
    session: requests.Session,
    *,
    timeout: float = 60.0,
    chunk_size: int = 1024 * 1024,
) -> tuple[Path, dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    url = dataset_url(dataset)
    archive_path = output_dir / f"publicextract.{dataset}.zip"
    partial_path = output_dir / f"publicextract.{dataset}.zip.part"

    existing_size = partial_path.stat().st_size if partial_path.exists() else 0
    headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
    response = session.get(url, headers=headers, stream=True, timeout=timeout)
    response.raise_for_status()

    resumed = existing_size > 0 and response.status_code == 206
    if existing_size and not resumed:
        existing_size = 0
    mode = "ab" if resumed else "wb"
    response_size = int(response.headers.get("Content-Length", "0") or 0)
    expected_size = existing_size + response_size if response_size else None
    downloaded = existing_size
    next_log = downloaded + 25 * 1024 * 1024
    started = time.monotonic()

    logging.info(
        "%s: downloading official archive%s",
        dataset,
        f" (resuming at {existing_size:,} bytes)" if resumed else "",
    )
    with partial_path.open(mode) as handle:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            handle.write(chunk)
            downloaded += len(chunk)
            if downloaded >= next_log:
                logging.info("%s: downloaded %s bytes", dataset, f"{downloaded:,}")
                next_log += 25 * 1024 * 1024

    if expected_size is not None and downloaded != expected_size:
        raise IOError(
            f"Incomplete download for {dataset}: expected {expected_size}, received {downloaded}"
        )
    os.replace(partial_path, archive_path)

    with zipfile.ZipFile(archive_path) as archive:
        corrupt_member = archive.testzip()
        if corrupt_member:
            raise zipfile.BadZipFile(f"Corrupt member in {dataset}: {corrupt_member}")

    elapsed = time.monotonic() - started
    logging.info("%s: verified %s bytes in %.1fs", dataset, f"{downloaded:,}", elapsed)
    metadata: dict[str, object] = {
        "dataset": dataset,
        "url": url,
        "archive": archive_path.name,
        "size_bytes": downloaded,
        "sha256": _sha256(archive_path),
        "last_modified": response.headers.get("Last-Modified", ""),
        "etag": response.headers.get("ETag", ""),
        "downloaded_at_epoch": int(time.time()),
    }
    return archive_path, metadata


def download_datasets(
    datasets: Iterable[str],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    extract: bool = True,
    timeout: float = 60.0,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = output_dir / "extracted"
    manifest_path = output_dir / "manifest.json"
    manifest: dict[str, object] = {
        "source": "Charity Commission for England and Wales daily public register extract",
        "format": "JSON",
        "trustee_extract_included": False,
        "datasets": {},
    }
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                manifest.update(existing)
        except (OSError, json.JSONDecodeError):
            logging.warning("Existing manifest could not be read; rebuilding it.")

    with requests.Session() as session:
        for dataset in datasets:
            if dataset not in DATASETS:
                raise ValueError(f"Unsupported or excluded dataset: {dataset}")
            archive_path, metadata = download_archive(
                dataset,
                output_dir,
                session,
                timeout=timeout,
            )
            if extract:
                extract_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(archive_path) as archive:
                    metadata["extracted_files"] = _safe_extract(archive, extract_dir)
            datasets_manifest = manifest.setdefault("datasets", {})
            if isinstance(datasets_manifest, dict):
                datasets_manifest[dataset] = metadata
            manifest["updated_at_epoch"] = int(time.time())
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download official Charity Commission daily public register extracts."
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=DATASETS,
        default=list(DATASETS),
        help="Datasets to download (defaults to all non-trustee datasets).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Local cache directory.",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Verify and retain ZIP archives without extracting them.",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    manifest = download_datasets(
        args.datasets,
        args.output_dir,
        extract=not args.skip_extract,
        timeout=args.timeout,
    )
    dataset_count = len(manifest.get("datasets", {}))
    logging.info("Completed official bulk download for %d dataset(s).", dataset_count)


if __name__ == "__main__":
    main()
