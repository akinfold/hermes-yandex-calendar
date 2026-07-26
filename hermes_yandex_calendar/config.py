"""Build a CalDAV client from the environment.

Secrets are resolved via :func:`get_provider_env` (env vars, then
``~/.hermes/.env``). Nothing here logs secret values.
"""

from __future__ import annotations

from ._compat import get_provider_env
from .caldav import DEFAULT_BASE_URL, YandexCalDAVClient

ENV_LOGIN = "YANDEX_CALENDAR_LOGIN"
ENV_PASSWORD = "YANDEX_CALENDAR_APP_PASSWORD"
ENV_BASE_URL = "YANDEX_CALENDAR_BASE_URL"
ENV_CALENDARS = "YANDEX_CALENDAR_CALENDARS"
ENV_ACTIONS = "YANDEX_CALENDAR_ACTIONS"

#: Every action the plugin can expose, in the order the tools are registered.
ACTIONS: tuple[str, ...] = (
    "list_calendars",
    "list_events",
    "create_event",
    "update_event",
    "respond_event",
    "move_event",
    "delete_event",
)

#: Shorthands accepted by ``YANDEX_CALENDAR_ACTIONS`` alongside single actions.
ACTION_GROUPS: dict[str, frozenset[str]] = {
    "all": frozenset(ACTIONS),
    "read": frozenset({"list_calendars", "list_events"}),
    "write": frozenset({"create_event", "update_event", "respond_event", "move_event"}),
    "delete": frozenset({"delete_event"}),
}

_TOOL_PREFIX = "yandex_calendar_"

__all__ = [
    "ACTIONS",
    "ACTION_GROUPS",
    "ENV_ACTIONS",
    "ENV_BASE_URL",
    "ENV_CALENDARS",
    "ENV_LOGIN",
    "ENV_PASSWORD",
    "allowed_actions",
    "allowed_calendars",
    "build_client",
    "credentials_present",
]


def allowed_calendars() -> list[str]:
    """The comma-separated allow-list of calendar names, or ``[]`` for all."""
    raw = get_provider_env(ENV_CALENDARS)
    return [c.strip() for c in raw.split(",") if c.strip()]


def allowed_actions() -> frozenset[str]:
    """The actions this deployment may perform, from ``YANDEX_CALENDAR_ACTIONS``.

    Accepts single actions (``create_event``), the group shorthands in
    :data:`ACTION_GROUPS` (``read``, ``write``, ``delete``, ``all``), and full tool
    names (``yandex_calendar_create_event``), comma-separated and case-insensitive.
    Unset or blank means every action, so existing installs are unaffected. Any
    other value is an explicit allow-list: names that match nothing are dropped
    rather than raising, so a typo can only ever withhold a tool, never grant one
    (a value naming nothing valid therefore allows nothing). Tools are filtered at
    registration time, so Hermes must be restarted for a change to take effect.
    """
    raw = get_provider_env(ENV_ACTIONS)
    if not raw.strip():
        return frozenset(ACTIONS)
    allowed: set[str] = set()
    for item in raw.split(","):
        key = item.strip().lower().replace("-", "_").removeprefix(_TOOL_PREFIX)
        if key in ACTION_GROUPS:
            allowed |= ACTION_GROUPS[key]
        elif key in ACTIONS:
            allowed.add(key)
    return frozenset(allowed)


class MissingCredentials(RuntimeError):
    """Raised when required Yandex credentials are absent from the environment."""


def credentials_present() -> bool:
    """Cheap check (no network) for the ``check_fn`` / availability gate."""
    return bool(get_provider_env(ENV_LOGIN) and get_provider_env(ENV_PASSWORD))


def build_client() -> YandexCalDAVClient:
    """Construct a :class:`YandexCalDAVClient` from environment credentials."""
    login = get_provider_env(ENV_LOGIN)
    password = get_provider_env(ENV_PASSWORD)
    if not login or not password:
        raise MissingCredentials(
            f"{ENV_LOGIN} and {ENV_PASSWORD} must be set "
            "(create an app password at https://id.yandex.ru/security/app-passwords)."
        )
    base_url = get_provider_env(ENV_BASE_URL) or DEFAULT_BASE_URL
    return YandexCalDAVClient(
        login=login,
        password=password,
        base_url=base_url,
        allowed_calendars=allowed_calendars(),
    )
