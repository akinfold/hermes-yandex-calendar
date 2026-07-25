# Contributing

Thanks for your interest in improving **hermes-yandex-calendar**.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
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
are absent.

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
