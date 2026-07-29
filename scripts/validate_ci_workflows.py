#!/usr/bin/env python3
"""Offline completeness/safety checks for GitHub workflow definitions."""

from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
CI_PATH = WORKFLOW_ROOT / "ci.yml"
STAGING_PATH = WORKFLOW_ROOT / "deploy-staging.yml"
PRODUCTION_PATH = WORKFLOW_ROOT / "deploy-production.yml"

REQUIRED_CI_MARKERS = frozenset(
    {
        "compileall",
        "flake8",
        "mypy --config-file",
        "pytest -q --cov",
        "npm run lint",
        "npm test",
        "npm run build",
        "npm run test:e2e",
        "gitleaks/gitleaks-action",
        "gh-action-pip-audit",
        "npm audit",
        "check_licenses.py",
        "github/codeql-action",
        "generate_sbom.py",
        "anchore/sbom-action",
        "docker build",
        "verify_container_image.sh",
        "aquasecurity/trivy-action",
        "terraform fmt -check",
        "terraform -chdir=infra/terraform/environments/staging validate",
        "alembic upgrade head",
        "test_sqlite_to_postgres_migration.py",
        "test_api_golden.py",
        "test_ci_performance_smoke.py",
    }
)


def validate() -> dict[str, object]:
    workflows = [CI_PATH, STAGING_PATH, PRODUCTION_PATH]
    for path in workflows:
        if not path.is_file():
            raise ValueError(f"Missing workflow: {path.name}")
    ci = CI_PATH.read_text(encoding="utf-8")
    staging = STAGING_PATH.read_text(encoding="utf-8")
    production = PRODUCTION_PATH.read_text(encoding="utf-8")
    combined = "\n".join((ci, staging, production))

    missing = sorted(marker for marker in REQUIRED_CI_MARKERS if marker not in ci)
    if missing:
        raise ValueError("Missing CI gates: " + ", ".join(missing))
    if "pull_request_target:" in combined:
        raise ValueError("pull_request_target is forbidden for untrusted code")
    if re.search(r"aws-(?:access-key-id|secret-access-key)", combined, re.IGNORECASE):
        raise ValueError("Long-lived AWS key workflow input is forbidden")
    if "terraform apply" in ci or "terraform destroy" in combined:
        raise ValueError("PR CI must not apply and no workflow may destroy infrastructure")
    if "--require-hashes" not in ci or "npm ci --ignore-scripts" not in ci:
        raise ValueError("Deterministic dependency installation is required")
    if re.search(r"(?m)^\s*(?:run:\s*)?pytest\b", ci):
        raise ValueError(
            "Invoke pytest through python -m so the repository root remains importable"
        )

    for action, reference in re.findall(r"uses:\s*([^@\s]+)@([^\s]+)", combined):
        if reference in {"main", "master", "latest"}:
            raise ValueError(f"Floating action reference is forbidden: {action}@{reference}")

    staging_markers = (
        "workflow_dispatch:",
        "I_APPROVE_STAGING",
        "environment: staging-publish",
        "environment: staging",
        "id-token: write",
        "aws-actions/configure-aws-credentials",
        "imageDigest",
        "api_image=${api_repository}@${api_digest}",
        "reviewed.tfplan",
        "terraform -chdir=${TF_ROOT} apply",
        "alembic",
        "update-service",
        "aws s3 sync",
        "http_load_smoke.py",
        "PostgreSQL reconciliation release gate",
        "npm run test:e2e",
        "previous-task-definitions.json",
    )
    missing_staging = [marker for marker in staging_markers if marker not in staging]
    if missing_staging:
        raise ValueError("Missing staging gates: " + ", ".join(missing_staging))
    if "if: ${{ false }}" not in production or "environment: production" not in production:
        raise ValueError("Production workflow must remain explicitly disabled and protected")

    return {
        "status": "passed",
        "workflow_files": len(workflows),
        "ci_gate_markers": len(REQUIRED_CI_MARKERS),
        "staging_requires_oidc": True,
        "staging_requires_protected_environments": ["staging-publish", "staging"],
        "production_enabled": False,
        "workflow_execution": "not_tested",
    }


def main() -> int:
    try:
        result = validate()
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
