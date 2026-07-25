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

__all__ = [
    "ENV_BASE_URL",
    "ENV_CALENDARS",
    "ENV_LOGIN",
    "ENV_PASSWORD",
    "allowed_calendars",
    "build_client",
    "credentials_present",
]


def allowed_calendars() -> list[str]:
    """The comma-separated allow-list of calendar names, or ``[]`` for all."""
    raw = get_provider_env(ENV_CALENDARS)
    return [c.strip() for c in raw.split(",") if c.strip()]


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
