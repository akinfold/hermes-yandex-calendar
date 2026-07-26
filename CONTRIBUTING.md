# Contributing

Thanks for your interest in improving **hermes-yandex-calendar** — a
[Hermes Agent](https://hermes-agent.nousresearch.com) plugin that manages
Yandex Calendar over CalDAV. Contributions of all sizes are welcome: bug
reports, docs, tests, and features.

All repository content — code, comments, docs, commit messages, issues, and
PRs — is in **English**. Be respectful and constructive; assume good intent.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Project layout

```
hermes_yandex_calendar/
  ical.py       # iCalendar (RFC 5545) parsing/serialization, no Hermes imports
  caldav.py     # CalDAV client for Yandex, no Hermes imports
  config.py     # env -> client, calendar allow-list, action allow-list
  _compat.py    # real-vs-shim host env helper
  tool.py       # tool schemas + handlers (JSON in, JSON string out)
  __init__.py   # register(ctx) — the plugin entry point
tests/          # unit tests (no network, httpx.MockTransport)
tests/e2e/      # live tests against a real account, marked `e2e`
```

## Ground rules

The plugin follows the Hermes plugin contract; a few of these are load-bearing:

- **Layering.** Keep Hermes-free domain logic (`caldav.py`, `ical.py`) free of any
  `agent.*` imports so it stays unit-testable. The host-facing glue lives in
  `tool.py`, `config.py`, and `__init__.py`.
- **Never raise across the boundary.** Tool handlers (`handle_*`) must always return
  a JSON string — every failure becomes `{"error": "..."}`. The CalDAV client raises
  `CalDAVError`, which the handlers translate.
- **Relative imports only** in `__init__.py` — the plugin loads as
  `hermes_plugins.yandex_calendar`.
- **Untrusted XML** from the server is parsed with `defusedxml`, never `xml.etree`.
- **Secrets** are resolved via `_compat.get_provider_env`; never log their values.

## Checks

```bash
ruff check . && ruff format --check .
pytest --cov=hermes_yandex_calendar --cov-fail-under=90
```

Unit tests must not hit the network — mock HTTP with `httpx.MockTransport`. Live
tests go under `tests/e2e/`, are marked `@pytest.mark.e2e`, and skip when credentials
are absent. To run them locally, put the credentials in files the e2e conftest picks
up (see `tests/e2e/conftest.py`) and run `pytest -m e2e`. Live tests are manual and
never required for a PR.

## Commit & PR conventions

- Focused commits with imperative subject lines (e.g. `caldav: keep VALARM blocks`).
- Open a PR against `main`, fill in the template, and link any related issue.
- CI (lint + tests on Python 3.11–3.13, coverage ≥ 90%) must pass.

## Reporting security issues

Please do not open a public issue for anything security-sensitive. Contact the
maintainer directly through the repository owner's GitHub profile.

## Releasing

Bump the version in **three** files that must stay in sync:

- `pyproject.toml`
- `hermes_yandex_calendar/__init__.py`
- `hermes_yandex_calendar/plugin.yaml`

Then tag:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

The publish workflow builds artifacts, creates a GitHub Release, and (if the repo
variable `PUBLISH_TO_PYPI=true` and a PyPI Trusted Publisher is configured) publishes
to PyPI.
