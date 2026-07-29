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

### Phase-12 type-check additions

The approved PyPI-only resolution added `mypy==2.3.0` to the development
input. `pip-compile` retained every existing version and added only mypy plus
its locked transitive packages: `ast-serialize==0.6.0`, `librt==0.13.0`,
`mypy-extensions==1.1.0` and `pathspec==1.1.1`. Every accepted platform wheel
and source archive SHA-256 is recorded in `requirements.txt`; local
installation used `pip install --require-hashes`. No runtime/container lock
changed, because the type checker is not a production dependency.

## npm

- Lock: `frontend/package-lock.json`, lockfile version 3.
- Install command: `npm ci --ignore-scripts`.
- Registry boundary: `https://registry.npmjs.org` only.
- Lifecycle scripts are disabled during both the verified local install and
  the Docker frontend build.

The package lock is the authoritative inventory of all resolved npm versions,
tarball URLs and integrity hashes.

### Phase-7 browser-test additions

The user approved one bounded npm-registry resolution and install for the named
browser/a11y gate. Browser binaries were not downloaded; Playwright uses the
already installed local Google Chrome. `npm install --package-lock-only` created
the reviewed lock delta, followed by `npm ci --ignore-scripts` with
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`.

| Package | Version | Integrity |
|---|---:|---|
| `@playwright/test` | `1.62.0` | `sha512-9zOJ6ZQRAena31MpOH9VSzIz8Ou3YJ/wtY/eQm5T2uhfhG7/U3COrMS8xOtUrZrp9OgdmzEnIYODye3nY1VqzA==` |
| `playwright` | `1.62.0` | `sha512-Z14dG305dgaLu6foB1TXQagFiW8JfSUIUaUuPaKQ6NtBPKF1P/qXcqfh6c6K/icPqdy37JmjbiBXf6JNg6Sylw==` |
| `playwright-core` | `1.62.0` | `sha512-nsNRyq0r2zsG8AcRHWknc9QRA5XCueC7gWMrs+Gx2tlZn9hcl8zudfh00lhJPY1DE7NmZ6bDsT9g2yey8mXljA==` |
| `@axe-core/playwright` | `4.12.1` | `sha512-rMd7xriptqKpP+w5265i4Hdkv2X5kbu6uiBi/B2I7uf3hieRBM3qDCfaKPtxfiYb2mKXfF+yLODJwIx+Jv1GDw==` |
| `axe-core` | `4.12.1` | `sha512-s7iGf5GaVMxEG0ENN9x+xTr7GFZCb1ZP/1uATUpCEK2X78nDB3RwbtFCo9pGAf9ru+VwoQ464DkaLEeRM08wJA==` |
| optional macOS `fsevents` | `2.3.2` | `sha512-xiqMQR4xAeHTuB9uWm+fFRcIOgKBMiOBP+eXiyT7jsgVCq1bkVygt00oASowB7EdtpOHaaPgKt812P9ab+DDKA==` |

No pre-existing package entry changed version. All tarballs resolve under
`https://registry.npmjs.org`; the full transitive inventory remains in
`frontend/package-lock.json`.

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

## Phase-4 rebuild record

The Phase-4 backend rebuild reused the unchanged hashed Python locks and the
digest-pinned Python base above. The legacy builder downloaded only the exact
locked artifacts from PyPI/files.pythonhosted.org; no additional dependency
version was resolved. No base-image pull was requested. The resulting local
arm64 image is
`sha256:e43491e5e7080e0923b9d777aa1f985bfd3c4897482d662d0be7bf7364758b91`
at 354,209,929 bytes. It has no repository digest because it is an unpushed
local build; this is not represented as a registry artifact.
