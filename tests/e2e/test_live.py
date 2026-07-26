"""Live e2e tests against a real Yandex Calendar account.

Deselected by default (``addopts = -m 'not e2e'``). Run explicitly with::

    YANDEX_CALENDAR_LOGIN=... YANDEX_CALENDAR_APP_PASSWORD=... pytest -m e2e

They create and then delete a throwaway event, so they leave no residue on a
successful run. Skipped automatically when credentials are absent.

Optional overrides: ``YC_E2E_MARKER`` (event summary prefix) and
``YC_E2E_ATTENDEE`` (the address invited to the throwaway event — must be a real
mailbox, see :func:`_attendee_email`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from hermes_yandex_calendar._compat import get_provider_env
from hermes_yandex_calendar.config import ENV_LOGIN, ENV_PASSWORD, build_client
from hermes_yandex_calendar.ical import Attendee, Event

pytestmark = pytest.mark.e2e


def _creds_available() -> bool:
    return bool(get_provider_env(ENV_LOGIN) and get_provider_env(ENV_PASSWORD))


requires_creds = pytest.mark.skipif(
    not _creds_available(),
    reason=f"set {ENV_LOGIN} and {ENV_PASSWORD} to run live e2e tests",
)

# ``ya.ru`` and ``yandex.ru`` are the same mailbox, so an alias of the test
# account is guaranteed to be deliverable.
_ALIAS_DOMAINS = {"ya.ru": "yandex.ru", "yandex.ru": "ya.ru"}


def _attendee_email() -> str:
    """A real, deliverable address to invite.

    Yandex mails an invitation to every ``ATTENDEE``, so a made-up address bounces
    back into the test account's inbox. Default to a sub-address of the account
    (``login+e2e@...``): mail is delivered to the same mailbox, but the address is
    not the account's own one — the server drops an attendee that equals the
    ``ORGANIZER``. Override with ``YC_E2E_ATTENDEE``.
    """
    override = os.environ.get("YC_E2E_ATTENDEE")
    if override:
        return override
    login = get_provider_env(ENV_LOGIN) or ""
    local, _, domain = login.partition("@")
    domain = _ALIAS_DOMAINS.get(domain.lower(), domain)
    return f"{local}+e2e@{domain}" if local and domain else login


@requires_creds
def test_discover_and_list():
    with build_client() as client:
        calendars = client.discover_calendars()
        assert calendars, "expected at least one calendar"
        now = datetime.now(UTC)
        client.list_events(now - timedelta(days=1), now + timedelta(days=30))


@requires_creds
def test_create_then_delete_roundtrip():
    marker = os.environ.get("YC_E2E_MARKER", "hermes-e2e")
    start = datetime.now(UTC) + timedelta(days=400)
    event = Event(
        uid="",
        summary=f"{marker} throwaway",
        start=start,
        end=start + timedelta(hours=1),
        description="Created by hermes-yandex-calendar e2e; safe to delete.",
    )
    with build_client() as client:
        created = client.create_event(event)
        assert created.href
        try:
            found = client.list_events(start - timedelta(hours=1), start + timedelta(hours=2))
            assert any(marker in e.summary for e in found)

            # edit it: add an attendee and mark it free
            fetched = client.get_event(created.href)
            assert fetched is not None
            guest = _attendee_email()
            fetched.attendees.append(Attendee(email=guest, name="Guest"))
            fetched.transp = "TRANSPARENT"
            client.update_event(fetched, created.href)

            reread = client.get_event(created.href)
            assert reread is not None
            returned = [a.email for a in reread.attendees]
            organizer = reread.organizer.email if reread.organizer else None
            assert any(a.email == guest for a in reread.attendees), (
                f"invited {guest}; server returned attendees={returned} organizer={organizer}"
            )
            assert reread.transp == "TRANSPARENT"
        finally:
            client.delete_event(created.href)


@requires_creds
def test_list_calendars_live():
    with build_client() as client:
        calendars = client.list_calendars()
        assert calendars
        assert all(c.href for c in calendars)
