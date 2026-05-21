# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies for development
pip install aiohttp

# Run all tests
python -m unittest discover tests/

# Run a single test file
python -m unittest tests/test_risco_cloud.py

# Run a single test case
python -m unittest tests.test_risco_cloud.TestRiscoCloud.test_login
```

## Architecture

Pyrisco provides two independent ways to communicate with Risco alarm systems:

**`RiscoCloud`** (`pyrisco/cloud/`) — connects via Risco's HTTPS cloud API (`riscocloud.com/webapi`). Login is a three-step process: username/password → access token, then site lookup → site ID, then PIN → session token. All subsequent requests are authenticated posts carrying both `Bearer` token and `sessionToken`. State can be fetched by polling (`get_state`) or via Server-Sent Events (`subscribe_states`), which calls back registered handlers whenever a `runtimeUpdate` event arrives with a newer timestamp. When the cloud API returns result code 72 it means the panel is unreachable; the library retries in "fallback mode" (`fromControlPanel: false`) to get the last known cached state, and sets `assumed_control_panel_state = True` on the returned `Alarm`.

**`RiscoLocal`** (`pyrisco/local/`) — opens a direct TCP socket to the alarm panel. The protocol is a proprietary binary framing with a rolling numeric command ID (1–49) and CRC validation, handled in `RiscoCrypt`. Authentication uses `LCL` then `RMT=<pin>`. After connecting, `RiscoLocal` queries panel capabilities (type, firmware, zone/partition counts) and initialises `Zone`, `Partition`, and `System` objects. Push updates arrive as unsolicited messages on the socket (parsed in `_listen`); `ZSTT*`, `PSTT*`, and `SSTT` prefixes indicate zone, partition, and system status changes respectively and invoke the registered handlers. A background keep-alive task sends `CLOCK` every 5 seconds.

**Shared abstractions** (`pyrisco/common.py`) define the `Partition`, `Zone`, and `System` base classes (abstract via `raise NotImplementedError`). Both backends implement these independently — there is no shared concrete base class. The three public exceptions (`UnauthorizedError`, `CannotConnectError`, `OperationError`) are also defined here.

**Indexing convention**: Cloud uses 0-based partition/zone IDs; Local uses 1-based IDs. Handlers registered with `add_*_handler` return a callable that removes the handler when called.

## Release flow

1. Ensure all changes are merged to the default branch.
2. Push a git tag matching the new version: `git tag v0.7.1 && git push --tags`
3. Create a GitHub Release from that tag (UI or `gh release create v0.7.1 --generate-notes`).
4. The `release.yml` GitHub Actions workflow triggers automatically, builds the package, and publishes to PyPI.
5. Version is derived from the git tag by `setuptools-scm` — no source file needs editing.

## PyPI Trusted Publisher

The workflow publishes via OIDC (no stored credentials). If setting up on a new PyPI account or project, configure the Trusted Publisher at:
https://pypi.org/manage/project/pyrisco/settings/publishing/
- Owner: OnFreund, Repo: pyrisco, Workflow: release.yml, Environment: pypi
