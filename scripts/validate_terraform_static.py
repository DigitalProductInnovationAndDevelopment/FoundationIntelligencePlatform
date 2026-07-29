#!/usr/bin/env python3
"""Offline Terraform structure/security checks when the Terraform CLI is absent."""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
TERRAFORM_ROOT = ROOT / "infra" / "terraform"
MODULE_ROOT = TERRAFORM_ROOT / "modules" / "platform"
REQUIRED_RESOURCE_TYPES = frozenset(
    {
        "aws_acm_certificate",
        "aws_appautoscaling_policy",
        "aws_budgets_budget",
        "aws_cloudfront_distribution",
        "aws_cloudwatch_dashboard",
        "aws_cloudwatch_log_group",
        "aws_cloudwatch_metric_alarm",
        "aws_db_instance",
        "aws_db_parameter_group",
        "aws_db_subnet_group",
        "aws_ecr_repository",
        "aws_ecs_cluster",
        "aws_ecs_service",
        "aws_ecs_task_definition",
        "aws_iam_openid_connect_provider",
        "aws_iam_role",
        "aws_internet_gateway",
        "aws_kms_key",
        "aws_lb",
        "aws_lb_target_group",
        "aws_nat_gateway",
        "aws_route53_record",
        "aws_s3_bucket",
        "aws_scheduler_schedule",
        "aws_security_group",
        "aws_sfn_state_machine",
        "aws_sns_topic",
        "aws_sqs_queue",
        "aws_subnet",
        "aws_vpc",
        "aws_vpc_endpoint",
        "aws_wafv2_web_acl",
    }
)
ALLOWED_WILDCARD_RESOURCE_CONTEXTS = frozenset(
    {
        "AccountAdministration",
        "CloudWatchLogsEncryption",
        "AuthenticateToECR",
        "ManageSynchronousTaskEvents",
        "WriteExecutionLogs",
        "PullImages",
        "CloudFrontReadFrontendObjects",
        "OperationalNotificationEncryption",
        "ManageSynchronousWorkerTask",
        "RegisterTaggedTaskDefinitions",
        "ObserveReleaseGate",
    }
)


def _structure_only(text: str) -> str:
    """Remove comments and quoted content, preserving only structural tokens."""
    output: list[str] = []
    index = 0
    in_string = False
    in_line_comment = False
    in_block_comment = False
    escaped = False
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if current == "\n":
                in_line_comment = False
                output.append(current)
        elif in_block_comment:
            if current == "*" and following == "/":
                in_block_comment = False
                index += 1
        elif in_string:
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == '"':
                in_string = False
        elif current == '"':
            in_string = True
        elif current == "#":
            in_line_comment = True
        elif current == "/" and following == "/":
            in_line_comment = True
            index += 1
        elif current == "/" and following == "*":
            in_block_comment = True
            index += 1
        else:
            output.append(current)
        index += 1
    if in_string or in_block_comment:
        raise ValueError("Unterminated Terraform string or block comment")
    return "".join(output)


def _balanced(path: Path, text: str) -> None:
    pairs = {"}": "{", ")": "(", "]": "["}
    stack: list[tuple[str, int]] = []
    for offset, token in enumerate(_structure_only(text)):
        if token in "{([":
            stack.append((token, offset))
        elif token in "})]":
            if not stack or stack[-1][0] != pairs[token]:
                raise ValueError(f"{path}: unmatched {token} at structural offset {offset}")
            stack.pop()
    if stack:
        raise ValueError(f"{path}: unmatched {stack[-1][0]} at structural offset {stack[-1][1]}")


def validate() -> dict[str, object]:
    files = sorted(TERRAFORM_ROOT.rglob("*.tf"))
    if not files:
        raise ValueError("No Terraform files found")
    contents = {path: path.read_text(encoding="utf-8") for path in files}
    for path, text in contents.items():
        _balanced(path, text)
        if "TODO" in text or "FIXME" in text:
            raise ValueError(f"{path}: placeholder marker is forbidden")
        if re.search(r"(?:AKIA|ASIA)[0-9A-Z]{16}", text):
            raise ValueError(f"{path}: AWS credential-shaped value detected")

    module_text = "\n".join(
        text for path, text in contents.items() if MODULE_ROOT in path.parents
    )
    resource_types = set(re.findall(r'\bresource\s+"([a-z0-9_]+)"\s+"', module_text))
    missing = sorted(REQUIRED_RESOURCE_TYPES - resource_types)
    if missing:
        raise ValueError(f"Missing required resource types: {', '.join(missing)}")

    required_contracts = {
        "private RDS": r"publicly_accessible\s*=\s*false",
        "RDS deletion protection": r"deletion_protection\s*=\s*true",
        "Terraform destroy guard": r"prevent_destroy\s*=\s*true",
        "S3 public ACL block": r"block_public_acls\s*=\s*true",
        "S3 public policy block": r"block_public_policy\s*=\s*true",
        "bucket owner enforcement": r'object_ownership\s*=\s*"BucketOwnerEnforced"',
        "KMS bucket encryption": r'kms_master_key_id\s*=\s*aws_kms_key\.platform\.arn',
        "non-destructive buckets": r"force_destroy\s*=\s*false",
        "immutable ECR tags": r'image_tag_mutability\s*=\s*"IMMUTABLE"',
        "immutable image validation": r'@sha256:\[0-9a-f\]\{64\}\$',
        "non-root API": r'user\s*=\s*"10001:10001"',
        "read-only API filesystem": r"readonlyRootFilesystem\s*=\s*true",
        "private ECS tasks": r"assign_public_ip\s*=\s*false",
        "OIDC audience pin": r'token\.actions\.githubusercontent\.com:aud',
        "OIDC subject pin": r'token\.actions\.githubusercontent\.com:sub',
    }
    for name, pattern in required_contracts.items():
        if not re.search(pattern, module_text):
            raise ValueError(f"Missing Terraform security contract: {name}")

    storage_text = (MODULE_ROOT / "storage.tf").read_text(encoding="utf-8")
    if re.search(r"\bexpiration\s*\{", storage_text):
        raise ValueError("Destructive S3 lifecycle expiration must remain disabled")
    if re.search(r'Action\s*=\s*"\*"', module_text):
        raise ValueError("Unbounded wildcard IAM action detected")

    wildcard_contexts: set[str] = set()
    current_sid = ""
    for line in module_text.splitlines():
        sid = re.search(r'Sid\s*=\s*"([^"]+)"', line)
        if sid:
            current_sid = sid.group(1)
        if re.search(r'Resource\s*=\s*"\*"', line):
            wildcard_contexts.add(current_sid)
    unknown_wildcards = sorted(wildcard_contexts - ALLOWED_WILDCARD_RESOURCE_CONTEXTS)
    if unknown_wildcards:
        raise ValueError(
            "Unjustified wildcard IAM resource contexts: " + ", ".join(unknown_wildcards)
        )

    for environment in ("dev", "staging"):
        environment_root = TERRAFORM_ROOT / "environments" / environment
        environment_text = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(environment_root.glob("*.tf"))
        )
        if f'environment = "{environment}"' not in environment_text:
            raise ValueError(f"{environment}: module environment is missing")
        if not re.search(r"enable_schedules\s*=\s*false", environment_text):
            raise ValueError(f"{environment}: schedules must fail closed")
        if not re.search(r'backend\s+"s3"\s*\{\s*encrypt\s*=\s*true\s*\}', environment_text):
            raise ValueError(f"{environment}: encrypted backend configuration point is missing")
        if re.search(r'\b(?:bucket|key|region)\s*=\s*"[^\"]+"', environment_text):
            raise ValueError(f"{environment}: live backend coordinates must not be committed")

    return {
        "status": "passed",
        "terraform_files": len(files),
        "resource_blocks": len(re.findall(r'\bresource\s+"', module_text)),
        "resource_types": len(resource_types),
        "environments": ["dev", "staging"],
        "provider_dependent_validation": "not_tested",
        "aws_actions_performed": False,
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
    sys.exit(main())
