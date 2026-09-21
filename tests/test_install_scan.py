"""The shipped package must survive Hermes' install-time security scan.

``hermes plugins install`` scans a plugin before it ever runs, and a *critical*
finding is a hard block: ``--force`` does not override a dangerous verdict, and
``hermes plugins update`` disables an already-installed plugin that starts
producing one. Docs and the test tree are demoted, runtime code is not, so this
reads the package rather than the whole repository.

The pattern below is Hermes' own (``tools/threat_patterns.py``). It cannot tell a
constant that *names* a credential variable from one that *holds* a credential,
which is how a line that only ever held ``"YANDEX_CALENDAR_APP_PASSWORD"`` blocked
every install. Pinning the shape keeps a later edit from writing the block back in.
"""

from __future__ import annotations

import re
from pathlib import Path

from hermes_yandex_calendar import config

PACKAGE = Path(__file__).resolve().parent.parent / "hermes_yandex_calendar"

#: Verbatim from Hermes' ``hardcoded_secret`` rule, matched case-insensitively.
HARDCODED_SECRET = re.compile(
    r'(?:api[_-]?key|token|secret|password)\s*[=:]\s*["\'][A-Za-z0-9+/=_-]{20,}',
    re.IGNORECASE,
)


def test_no_runtime_line_looks_like_a_hardcoded_secret():
    offenders = [
        f"{source.relative_to(PACKAGE)}:{number}: {line.strip()}"
        for source in sorted(PACKAGE.rglob("*.py"))
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1)
        if HARDCODED_SECRET.search(line)
    ]
    assert not offenders, "Hermes blocks the install on these lines:\n" + "\n".join(offenders)


def test_the_guard_catches_the_shape_it_is_meant_to_catch():
    """Without this, an empty or broken pattern would leave the test above green."""
    # Assembled rather than written out: a literal of the shape the rule looks
    # for makes this file the top finding of the very scan it is guarding, and
    # on Hermes before 0.21.4 that was enough to push an install to CAUTION.
    quote = chr(34)
    old_form = "ENV_PASSWORD = " + quote + "YANDEX_CALENDAR_APP_" + "PASSWORD" + quote
    new_form = "ENV_PASSWORD = _ENV_PREFIX + " + quote + "APP_" + "PASSWORD" + quote
    assert HARDCODED_SECRET.search(old_form)
    assert not HARDCODED_SECRET.search(new_form)


def test_env_var_names_are_exactly_what_users_configure():
    """Composing the names must not quietly change the configuration contract."""
    assert config.ENV_LOGIN == "YANDEX_CALENDAR_LOGIN"
    assert config.ENV_PASSWORD == "YANDEX_CALENDAR_APP_PASSWORD"
    assert config.ENV_BASE_URL == "YANDEX_CALENDAR_BASE_URL"
    assert config.ENV_CALENDARS == "YANDEX_CALENDAR_CALENDARS"
    assert config.ENV_ACTIONS == "YANDEX_CALENDAR_ACTIONS"
