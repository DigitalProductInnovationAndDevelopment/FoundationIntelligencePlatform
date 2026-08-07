#!/usr/bin/env python3
"""Fail on strong-copyleft runtime/development packages using local metadata."""

from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import re
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r"(?:^|[^A-Z])(?:AGPL|SSPL|(?<!L)GPL)(?:[^A-Z]|$)", re.IGNORECASE)


def _python_licenses() -> Iterable[tuple[str, str, str]]:
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        version = distribution.version
        declared = distribution.metadata.get("License-Expression") or distribution.metadata.get(
            "License", ""
        )
        classifiers = [
            item.removeprefix("License :: ")
            for item in distribution.metadata.get_all("Classifier", [])
            if item.startswith("License :: ")
        ]
        license_text = str(declared or " OR ".join(classifiers) or "UNKNOWN")
        yield str(name), str(version), license_text


def _node_licenses(node_modules: Path) -> Iterable[tuple[str, str, str]]:
    for path in sorted(node_modules.rglob("package.json")):
        relative_parts = path.relative_to(node_modules).parts
        is_top_level_package = (
            len(relative_parts) == 2
            or (
                len(relative_parts) == 3
                and relative_parts[0].startswith("@")
            )
        )
        if not is_top_level_package or ".bin" in path.parts:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        name = str(payload.get("name", path.parent.name))
        version = str(payload.get("version", "unknown"))
        declared = payload.get("license", "UNKNOWN")
        if isinstance(declared, dict):
            declared = declared.get("type", "UNKNOWN")
        yield name, version, str(declared)


def scan(node_modules: Path) -> dict[str, object]:
    records = list(_python_licenses()) + list(_node_licenses(node_modules))
    forbidden = sorted(
        f"{name}@{version}: {license_name}"
        for name, version, license_name in records
        if FORBIDDEN.search(license_name)
    )
    unknown = sum(1 for _, _, license_name in records if license_name == "UNKNOWN")
    return {
        "status": "failed" if forbidden else "passed",
        "components_scanned": len(records),
        "unknown_license_metadata": unknown,
        "forbidden": forbidden,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--node-modules",
        type=Path,
        default=ROOT / "frontend" / "node_modules",
    )
    args = parser.parse_args()
    result = scan(args.node_modules)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
