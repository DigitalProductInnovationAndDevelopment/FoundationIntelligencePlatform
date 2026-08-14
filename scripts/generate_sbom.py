#!/usr/bin/env python3
"""Generate deterministic CycloneDX JSON SBOMs from committed lockfiles."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
PYTHON_LOCK = ROOT / "requirements.txt"
NPM_LOCK = ROOT / "frontend" / "package-lock.json"


def _component(name: str, version: str, ecosystem: str) -> dict[str, Any]:
    normalized_name = name.replace("_", "-").lower() if ecosystem == "pypi" else name
    escaped_name = quote(normalized_name, safe="@/")
    purl = f"pkg:{ecosystem}/{escaped_name}@{quote(version, safe='')}"
    return {
        "type": "library",
        "bom-ref": purl,
        "name": normalized_name,
        "version": version,
        "purl": purl,
    }


def python_components(path: Path = PYTHON_LOCK) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        requirement = re.match(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)", line)
        if requirement:
            current = _component(requirement.group(1), requirement.group(2), "pypi")
            current["hashes"] = []
            components.append(current)
            continue
        digest = re.search(r"--hash=sha256:([0-9a-f]{64})", line)
        if digest and current is not None:
            current["hashes"].append({"alg": "SHA-256", "content": digest.group(1)})
    for component in components:
        if not component["hashes"]:
            raise ValueError(f"Python component lacks a locked hash: {component['name']}")
    return sorted(components, key=lambda item: (item["name"], item["version"]))


def _npm_name(package_path: str, record: dict[str, Any]) -> str:
    if record.get("name"):
        return str(record["name"])
    return package_path.rsplit("node_modules/", 1)[-1]


def _integrity_hash(value: str) -> dict[str, str] | None:
    if "-" not in value:
        return None
    algorithm, encoded = value.split("-", 1)
    supported = {"sha256": "SHA-256", "sha384": "SHA-384", "sha512": "SHA-512"}
    if algorithm not in supported:
        return None
    try:
        content = base64.b64decode(encoded, validate=True).hex()
    except ValueError as exc:
        raise ValueError("Invalid npm lockfile integrity value") from exc
    return {"alg": supported[algorithm], "content": content}


def npm_components(path: Path = NPM_LOCK) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("lockfileVersion", 0)) < 3:
        raise ValueError("npm lockfile version 3 or newer is required")
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for package_path, record in payload.get("packages", {}).items():
        if not package_path or "node_modules/" not in package_path:
            continue
        version = str(record.get("version", "")).strip()
        if not version:
            raise ValueError(f"npm package lacks a version: {package_path}")
        name = _npm_name(str(package_path), record)
        key = (name, version)
        component = unique.setdefault(key, _component(name, version, "npm"))
        integrity = _integrity_hash(str(record.get("integrity", "")))
        if integrity:
            component.setdefault("hashes", [])
            if integrity not in component["hashes"]:
                component["hashes"].append(integrity)
        if record.get("dev") or record.get("optional"):
            component["scope"] = "optional"
    return sorted(unique.values(), key=lambda item: (item["name"], item["version"]))


def document(application: str, components: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": application,
                "version": "1.0.0",
            },
            "properties": [
                {"name": "generation.source", "value": "committed-lockfile"},
                {"name": "generation.reproducible", "value": "true"},
            ],
        },
        "components": components,
    }


def write_document(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=ROOT / "docs" / "remediation" / "evidence" / "sbom",
    )
    args = parser.parse_args()
    backend = document("foundation-intelligence-backend", python_components())
    frontend = document("foundation-intelligence-frontend", npm_components())
    write_document(args.output_directory / "backend.cdx.json", backend)
    write_document(args.output_directory / "frontend.cdx.json", frontend)
    print(
        json.dumps(
            {
                "status": "passed",
                "backend_components": len(backend["components"]),
                "frontend_components": len(frontend["components"]),
                "output_directory": str(args.output_directory),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
