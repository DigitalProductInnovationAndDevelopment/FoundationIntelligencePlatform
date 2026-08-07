# Retention, Privacy and Data-Subject Guide

## Safety baseline

`config/data-governance.json` is authoritative for the current proposed
configuration. Destructive deletion and production activation are both
disabled. Every `delete_after_days` value is `null`. PostgreSQL independently
requires approval, successful restore evidence and no holds for any future
destructive retention action; the initial deletion-manifest table additionally
accepts only dry-run report/archive actions.

There is deliberately no destructive HTTP route, cleanup command or scheduled
delete worker.

## Dry-run lifecycle

1. Identify a target, retention class, last-modified time, record/object/byte
   counts and available SHA-256 checksums.
2. Check exact, retention-class and global legal/incident holds.
3. Produce `report` or `archive` with `dry_run=true`; a hold produces `held`.
4. Store an auditable `retention_actions` record and append-only
   `deletion_manifests` evidence.
5. Do not change or delete the target.

Archive correction means a new report/manifest; evidence cannot be edited.
Export expiry likewise produces a report. `hold_until` suppresses expiry
reporting while active.

## Holds

Legal and incident holds contain type, scoped target, reason, actor and time.
They may scope one target, a retention class or all data. Only an
administrator can create or release a hold, and both actions require an
idempotency key and audit reason. Holds override retention even after a window
is reached.

## Restore-before-delete

Any later destructive design must reference an append-only
`restore_verifications` record with target, backup reference, backup checksum,
verifier, result and evidence. The current planner rejects deletion before
evaluating this prerequisite because global deletion is disabled. Recording a
restore test never enables deletion by itself.

## Data-subject workflow

1. Receive and classify access, correction, deletion, restriction or objection.
2. Persist only a SHA-256 subject reference at intake.
3. Verify identity using an approved private process.
4. Locate scoped records without exporting unrelated data.
5. Check legal/incident holds and source/legal constraints.
6. Record an approval or rejection and the responsible handler.
7. Execute a mutation only under a separately approved, audited procedure.
8. Retain the audit reference and notify the requester through an approved
   private channel.

This repository implements intake/control state, not an identity-verification
service or an approved deletion executor.

## Log redaction

Structured redaction replaces credentials, Authorization/cookies, connection
strings, emails, postal addresses, raw payloads and article bodies. It recurses
through objects and arrays before administrative output is serialized. Plain
log text additionally removes credential-shaped values, database passwords,
AWS-key shapes, model-key shapes and email addresses.

Logs must never contain full third-party article bodies, raw Authorization
headers or unredacted personal/contact fields.

## Backup, PITR, RTO and RPO

- Backup policy: proposed; encryption and restore tests required. Retention and
  cross-account copy remain unresolved.
- PostgreSQL PITR: required by proposed policy; retention period unresolved.
- RTO: unresolved.
- RPO: unresolved.

Terraform may encode fail-safe defaults, but these values must not be presented
as approved service objectives until owners decide them and deployment evidence
exists.
