"""Live e2e tests against a real Yandex Calendar account.

Deselected by default (``addopts = -m 'not e2e'``). Run explicitly with::

    YANDEX_CALENDAR_LOGIN=... YANDEX_CALENDAR_APP_PASSWORD=... pytest -m e2e

They create and then delete a throwaway event, so they leave no residue on a
successful run. Skipped automatically when credentials are absent.

Optional overrides: ``YC_E2E_MARKER`` (event summary prefix) and
``YC_E2E_ATTENDEES`` (comma-separated addresses invited to the throwaway event —
see :func:`_attendee_emails`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from hermes_yandex_calendar._compat import get_provider_env
from hermes_yandex_calendar.caldav import normalize_email
from hermes_yandex_calendar.config import ENV_LOGIN, ENV_PASSWORD, build_client
from hermes_yandex_calendar.ical import Attendee, Event

pytestmark = pytest.mark.e2e


def _creds_available() -> bool:
    return bool(get_provider_env(ENV_LOGIN) and get_provider_env(ENV_PASSWORD))


requires_creds = pytest.mark.skipif(
    not _creds_available(),
    reason=f"set {ENV_LOGIN} and {ENV_PASSWORD} to run live e2e tests",
)

ENV_ATTENDEES = "YC_E2E_ATTENDEES"


def _default_attendees() -> list[str]:
    """A sub-address of the test account, used when ``YC_E2E_ATTENDEES`` is unset.

    Delivered to the account's own mailbox, but the ``+e2e`` local part keeps it a
    distinct attendee — the server drops one that resolves to the ORGANIZER.
    """
    login = normalize_email(get_provider_env(ENV_LOGIN) or "")
    local, sep, domain = login.rpartition("@")
    return [f"{local}+e2e@{domain}"] if sep else []


def _attendee_emails() -> list[str]:
    """Real, deliverable addresses to invite to the throwaway event.

    Yandex mails an invitation to every ``ATTENDEE``, so made-up addresses bounce
    back into the test account's inbox — and an address that resolves to the
    account itself is dropped by the server as a self-invite (it equals the
    ``ORGANIZER``; note that Yandex rewrites ``@ya.ru`` to ``@yandex.ru``).

    The addresses come from ``YC_E2E_ATTENDEES`` (comma-separated), supplied in CI
    by the ``yandex-calendar-e2e`` environment secret of the same name. Without it,
    fall back to a sub-address of the account so a fresh checkout still runs.
    """
    raw = os.environ.get(ENV_ATTENDEES, "")
    listed = [addr.strip() for addr in raw.split(",") if addr.strip()]
    return listed or _default_attendees()


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
            guests = _attendee_emails()
            assert guests, f"no invitee addresses: set {ENV_ATTENDEES}"
            for i, guest in enumerate(guests, start=1):
                fetched.attendees.append(Attendee(email=guest, name=f"Guest {i}"))
            fetched.transp = "TRANSPARENT"
            client.update_event(fetched, created.href)

            reread = client.get_event(created.href)
            assert reread is not None
            returned = {normalize_email(a.email) for a in reread.attendees}
            organizer = reread.organizer.email if reread.organizer else None
            missing = [g for g in guests if normalize_email(g) not in returned]
            assert not missing, (
                f"invited {guests}; missing after round-trip: {missing}; "
                f"server returned attendees={sorted(returned)} organizer={organizer}"
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
