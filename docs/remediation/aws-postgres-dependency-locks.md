# Dependency and Base-Image Lock Record

Updated: 2026-07-29

## Python

- Resolver bootstrap: `requirements-locking.txt`, including hashes for every
  artifact selected by the bootstrap lock.
- Production input: `requirements-runtime.in`.
- Production transitive hash lock: `requirements-runtime.txt`.
- Development/test input: `requirements.in`.
- Development/test transitive hash lock: `requirements.txt`.
- Resolver: Python 3.12, `pip-tools==7.5.2`, `pip==25.3`.
- Install policy: `pip install --require-hashes`; the container wheel stage also
  uses `--require-hashes`, `--no-deps` and `--no-build-isolation`.
- Index boundary: `https://pypi.org/simple` and artifacts from
  `files.pythonhosted.org` only.

The generated lockfiles are the authoritative inventory of every resolved
Python version and accepted artifact hash. `pip 26.1.2` was rejected after a
local resolver attempt because it is incompatible with `pip-tools 7.5.2`;
the bootstrap lock pins the compatible `pip 25.3` release.

## npm

- Lock: `frontend/package-lock.json`, lockfile version 3.
- Install command: `npm ci --ignore-scripts`.
- Registry boundary: `https://registry.npmjs.org` only.
- Lifecycle scripts are disabled during both the verified local install and
  the Docker frontend build.

The package lock is the authoritative inventory of all resolved npm versions,
tarball URLs and integrity hashes.

## OCI base images

The following versioned multi-platform manifest digests were returned by
Docker Hub and are pinned directly in `Dockerfile` or `docker-compose.yml`:

| Purpose | Tag | Manifest digest |
|---|---|---|
| Dockerfile frontend | `docker/dockerfile:1.8` | `sha256:e87caa74dcb7d46cd820352bfea12591f3dba3ddc4285e19c7dcd13359f7cefd` |
| Backend build/runtime | `python:3.12.13-slim-bookworm` | `sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b` |
| Frontend build | `node:22.22.2-alpine` | `sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f` |
| Frontend runtime | `nginxinc/nginx-unprivileged:1.30.4-alpine3.24` | `sha256:44e36330f74d4f3a1d4e222acca9e23b401fb87811a7597024502bb759c4dd49` |
| Local PostgreSQL | `postgres:16.14-alpine3.24` | `sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777` |

Local pull verification selected the `linux/arm64` variants. The pinned values
are manifest-list digests; `docker-bake.hcl` declares both `linux/amd64` and
`linux/arm64` targets.
