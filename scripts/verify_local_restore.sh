#!/usr/bin/env bash
set -euo pipefail

# Full logical backup/restore proof for the local Docker PostgreSQL service.
# The temporary database and archive are always removed; the source is read-only.

source_database="foundation_intelligence"
restore_database="fip_phase13_restore_check_20260729"
archive="/private/tmp/fip-phase13-restore-20260729.dump"

if [[ "${restore_database}" != fip_phase13_restore_check_* ]]; then
  echo "Refusing an unexpected restore database name" >&2
  exit 2
fi
if [[ "${archive}" != /private/tmp/fip-phase13-restore-*.dump ]]; then
  echo "Refusing an unexpected archive path" >&2
  exit 2
fi

compose=(docker-compose)
postgres=("${compose[@]}" exec -T postgres)

cleanup() {
  "${postgres[@]}" dropdb --if-exists --force -U foundation_app "${restore_database}" >/dev/null
  rm -f -- "${archive}"
}
trap cleanup EXIT

existing=$("${postgres[@]}" psql -U foundation_app -d postgres -Atc \
  "SELECT COUNT(*) FROM pg_database WHERE datname='${restore_database}'")
if [[ "${existing}" != "0" ]]; then
  echo "Temporary restore database already exists" >&2
  exit 3
fi

"${postgres[@]}" pg_dump \
  -U foundation_app \
  -d "${source_database}" \
  --format=custom \
  --compress=zstd:3 \
  --no-owner \
  --no-privileges > "${archive}"

archive_sha256=$(shasum -a 256 "${archive}" | awk '{print $1}')
archive_bytes=$(stat -f '%z' "${archive}")

"${postgres[@]}" createdb -U foundation_app "${restore_database}"
"${postgres[@]}" pg_restore \
  -U foundation_app \
  -d "${restore_database}" \
  --exit-on-error \
  --no-owner \
  --no-privileges < "${archive}"

control_sql="
SELECT json_build_object(
  'schema_version', (SELECT version_num FROM alembic_version),
  'active_dataset', (SELECT dataset_version FROM dataset_versions WHERE is_active),
  'charities', (SELECT COUNT(*) FROM charities WHERE dataset_version=(SELECT dataset_version FROM dataset_versions WHERE is_active)),
  'registry', (SELECT COUNT(*) FROM charity_registry_organizations WHERE dataset_version=(SELECT dataset_version FROM dataset_versions WHERE is_active)),
  'grants', (SELECT COUNT(*) FROM grants WHERE dataset_version=(SELECT dataset_version FROM dataset_versions WHERE is_active)),
  'overview_total_eur_minor', (SELECT COALESCE(SUM(eur_amount_minor),0) FROM grant_overview_facts WHERE dataset_version=(SELECT dataset_version FROM dataset_versions WHERE is_active) AND eur_amount_status NOT IN ('missing','invalid','negative')),
  'materialization', (SELECT COUNT(*) FROM materialization_versions WHERE dataset_version=(SELECT dataset_version FROM dataset_versions WHERE is_active) AND is_active AND status='active')
)::text;
"
source_controls=$("${postgres[@]}" psql -U foundation_app -d "${source_database}" -Atc "${control_sql}")
restore_controls=$("${postgres[@]}" psql -U foundation_app -d "${restore_database}" -Atc "${control_sql}")

if [[ "${source_controls}" != "${restore_controls}" ]]; then
  echo "Restored database controls differ from source" >&2
  exit 4
fi

python3 -c 'import json,sys; print(json.dumps({"status":"passed","restore_type":"full_logical_pg_dump_pg_restore","archive_sha256":sys.argv[1],"archive_bytes":int(sys.argv[2]),"controls":json.loads(sys.argv[3]),"temporary_database":sys.argv[4],"temporary_database_removed":True,"archive_removed":True,"aws_actions_performed":False},sort_keys=True))' \
  "${archive_sha256}" "${archive_bytes}" "${restore_controls}" "${restore_database}"
