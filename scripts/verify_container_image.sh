#!/bin/sh
set -eu

image_name=${1:-foundation-intelligence-backend:local}
maximum_bytes=${MAXIMUM_IMAGE_BYTES:-524288000}

image_user=$(docker image inspect --format '{{.Config.User}}' "$image_name")
case "$image_user" in
  ""|0|0:0|root)
    echo "FAIL: image user is privileged: ${image_user:-unset}" >&2
    exit 1
    ;;
esac

healthcheck=$(docker image inspect --format '{{json .Config.Healthcheck.Test}}' "$image_name")
if [ "$healthcheck" = "null" ] || [ "$healthcheck" = "[]" ]; then
  echo "FAIL: image has no healthcheck" >&2
  exit 1
fi

image_size=$(docker image inspect --format '{{.Size}}' "$image_name")
if [ "$image_size" -gt "$maximum_bytes" ]; then
  echo "FAIL: image size ${image_size} exceeds ${maximum_bytes} bytes" >&2
  exit 1
fi

temporary_directory=$(mktemp -d "${TMPDIR:-/tmp}/fip-image-check.XXXXXX")
container_id=""
cleanup() {
  if [ -n "$container_id" ]; then
    docker container rm "$container_id" >/dev/null 2>&1 || true
  fi
  rm -rf "$temporary_directory"
}
trap cleanup EXIT INT TERM

container_id=$(docker container create "$image_name")
docker container export "$container_id" >"$temporary_directory/rootfs.tar"
docker container rm "$container_id" >/dev/null
container_id=""

tar -tf "$temporary_directory/rootfs.tar" >"$temporary_directory/files.txt"
if grep -E '(^|/)(\.env($|\.)|[^/]+\.(db|sqlite|sqlite3)$)' "$temporary_directory/files.txt"; then
  echo "FAIL: image contains an environment or SQLite file" >&2
  exit 1
fi
if grep -E '^(app|home|root)/(.*/)?(\.aws|\.netrc|\.npmrc|\.pypirc|credentials|id_(rsa|ecdsa|ed25519)|[^/]+\.(pem|key|p12|pfx))(/|$)' "$temporary_directory/files.txt"; then
  echo "FAIL: image contains an application credential or private-key file" >&2
  exit 1
fi
if grep -E '^app/src/data/(raw|processed|preprocessed)/' "$temporary_directory/files.txt"; then
  echo "FAIL: image contains a domain-data payload" >&2
  exit 1
fi
if grep -E '(^|/)(gcc|g\+\+|cc|make)$' "$temporary_directory/files.txt"; then
  echo "FAIL: runtime image contains a compiler toolchain" >&2
  exit 1
fi

if [ -f docker-bake.hcl ]; then
  grep -F '"linux/amd64"' docker-bake.hcl >/dev/null
  grep -F '"linux/arm64"' docker-bake.hcl >/dev/null
else
  echo "FAIL: docker-bake.hcl is missing" >&2
  exit 1
fi

echo "PASS: user=$image_user size_bytes=$image_size healthcheck=present data_files=absent multiarch=declared"
