"""Live e2e tests against a real Yandex Calendar account.

Deselected by default (``addopts = -m 'not e2e'``). Run explicitly with::

    YANDEX_CALENDAR_LOGIN=... YANDEX_CALENDAR_APP_PASSWORD=... pytest -m e2e

They create, edit, move and then delete throwaway events, so they leave no residue
on a successful run. Skipped automatically when credentials are absent.

Optional overrides: ``YC_E2E_MARKER`` (event summary prefix) and
``YC_E2E_ATTENDEES`` (comma-separated addresses invited to the throwaway event —
see :func:`_attendee_emails`).
"""

from __future__ import annotations

import copy
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from hermes_yandex_calendar._compat import get_provider_env
from hermes_yandex_calendar.caldav import CalDAVError, normalize_email
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


@requires_creds
def test_concurrent_change_is_refused():
    """The server must honour If-Match: a write against a stale ETag is rejected.

    Proves the optimistic-concurrency guard end to end — a unit test can only show
    that the header is sent, not that Yandex acts on it.
    """
    marker = os.environ.get("YC_E2E_MARKER", "hermes-e2e")
    start = datetime.now(UTC) + timedelta(days=401)
    event = Event(
        uid="",
        summary=f"{marker} concurrency",
        start=start,
        end=start + timedelta(hours=1),
        description="Created by hermes-yandex-calendar e2e; safe to delete.",
    )
    with build_client() as client:
        created = client.create_event(event)
        try:
            first = client.get_event(created.href)
            assert first is not None
            if not first.etag:
                pytest.skip("this server does not expose ETags for events")
            stale = copy.deepcopy(first)

            first.summary = f"{marker} concurrency (updated)"
            client.update_event(first, created.href)

            stale.summary = f"{marker} concurrency (stale write)"
            with pytest.raises(CalDAVError, match="changed on the server"):
                client.update_event(stale, created.href)

            reread = client.get_event(created.href)
            assert reread is not None
            assert reread.summary.endswith("(updated)")  # the stale write did not land
        finally:
            client.delete_event(created.href)


@requires_creds
def test_move_between_calendars():
    """A move must land the event in the target and clear the source.

    Covers the conditional delete from the happy side: the original goes only when
    it still matches the copy, and that must not get in the way of a plain move.
    The target is probed first — an account can expose a calendar that accepts a
    PUT without storing it (holidays and birthdays are served that way), and that
    would fail this test for a reason that has nothing to do with moving.
    """
    marker = os.environ.get("YC_E2E_MARKER", "hermes-e2e")
    start = datetime.now(UTC) + timedelta(days=402)

    def throwaway(suffix: str) -> Event:
        return Event(
            uid="",
            summary=f"{marker} {suffix}",
            start=start,
            end=start + timedelta(hours=1),
            description="Created by hermes-yandex-calendar e2e; safe to delete.",
        )

    with build_client() as client:
        calendars = client.list_calendars()
        target = None
        for candidate in calendars[1:]:
            try:
                probe = client.create_event(throwaway("probe"), calendar=candidate.href)
            except CalDAVError:
                continue
            stored = client.get_event(probe.href) is not None
            client.delete_event(probe.href)
            if stored:
                target = candidate
                break
        if target is None:
            pytest.skip("no second calendar that accepts an event and returns it")

        created = client.create_event(throwaway("move"), calendar=calendars[0].href)
        moved = None
        try:
            moved = client.move_event(created.href, target.href)
            assert moved.href != created.href
            reread = client.get_event(moved.href)
            if reread is None:
                window = (start - timedelta(hours=1), start + timedelta(hours=2))
                listing = client.list_events(*window, calendar=target.href)
                names = sorted((e.href or "").rsplit("/", 1)[-1] for e in listing)
                raise AssertionError(f"copy not readable at {moved.href}; target holds {names}")
            assert reread.summary.endswith("move")
            assert client.get_event(created.href) is None  # the original is gone
        finally:
            client.delete_event((moved or created).href)


def _unfolded(text: str) -> list[str]:
    """Undo RFC 5545 folding so a line can be matched as one string.

    The server is free to fold where it likes, so comparing raw text would fail
    for reasons that have nothing to do with what is being tested.
    """
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _server_copy(client, href: str) -> list[str]:
    """The resource as the SERVER holds it, unfolded, without going through our parser.

    Reading it back with ``get_event`` would prove nothing here: our parser
    resolves a TZID into an instant, so a server that had normalised the event
    into UTC would still hand back the right moment and the test would pass.
    """
    document = client._fetch_document(href)  # private on purpose: the raw text is the point
    assert document is not None, "the event vanished from the server"
    return _unfolded(document[0])


def _vevent(lines: list[str]) -> list[str]:
    """Only the VEVENT's own lines.

    A VTIMEZONE carries DTSTART lines of its own, one per STANDARD/DAYLIGHT
    rule, and they come first in the document — searching the whole thing for
    "DTSTART" finds a daylight-saving rule from 1981, not the event.
    """
    start = next(i for i, line in enumerate(lines) if line.upper().startswith("BEGIN:VEVENT"))
    end = next(i for i, line in enumerate(lines) if line.upper().startswith("END:VEVENT"))
    return lines[start : end + 1]


def _dtstart(lines: list[str]) -> str:
    return next(line for line in _vevent(lines) if line.upper().startswith("DTSTART"))


def _assert_still_zoned(lines: list[str], tzid: str, local: str) -> None:
    """The three things that say the zone survived the round trip."""
    assert any(line.upper().startswith("BEGIN:VTIMEZONE") for line in lines), (
        "the server dropped the VTIMEZONE component"
    )
    assert f"TZID:{tzid}" in lines, f"the server did not keep the {tzid} definition"
    dtstart = _dtstart(lines)
    assert f"TZID={tzid}" in dtstart, f"DTSTART lost its zone: {dtstart}"
    assert dtstart.endswith(local), f"DTSTART is not the local time we wrote: {dtstart}"
    # The one that catches a server normalising to UTC: the instant would still
    # be right, so every positive check above could pass while the anchoring —
    # the whole point — was gone.
    assert not dtstart.rstrip().endswith("Z"), f"the server normalised DTSTART to UTC: {dtstart}"


@requires_creds
def test_a_zoned_recurring_event_keeps_its_zone_on_the_server():
    """Does Yandex store a local time with its zone, and keep it across an edit?

    Everything else in this suite reads events back through our own parser, which
    hides exactly the failure that matters. This one compares what the server
    holds. The event is a weekly series anchored to Europe/Berlin and starting
    before the spring changeover: if the zone is lost anywhere along the way, the
    stored series silently moves an hour from late March onwards.
    """
    marker = os.environ.get("YC_E2E_MARKER", "hermes-e2e")
    tzid = "Europe/Berlin"
    local = "20260302T100000"
    body = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//hermes-yandex-calendar//e2e//EN\r\n"
        f"BEGIN:VTIMEZONE\r\nTZID:{tzid}\r\n"
        "BEGIN:STANDARD\r\nDTSTART:19701025T030000\r\n"
        "TZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nEND:STANDARD\r\n"
        "BEGIN:DAYLIGHT\r\nDTSTART:19700329T020000\r\n"
        "TZOFFSETFROM:+0100\r\nTZOFFSETTO:+0200\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nEND:DAYLIGHT\r\n"
        "END:VTIMEZONE\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{marker}-zoned\r\n"
        f"SUMMARY:{marker} zoned series\r\n"
        "DESCRIPTION:Created by hermes-yandex-calendar e2e; safe to delete.\r\n"
        f"DTSTART;TZID={tzid}:{local}\r\n"
        f"DTEND;TZID={tzid}:20260302T103000\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    with build_client() as client:
        collection = client.resolve_calendar_href(None).rstrip("/") + "/"
        href = f"{collection}{marker}-zoned.ics"
        resp = client._request(  # private: we write a document we control byte for byte
            "PUT",
            href,
            content=body,
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        assert resp.status_code in (200, 201, 204), f"PUT failed: HTTP {resp.status_code}"
        try:
            # 1. What the server made of what we wrote.
            _assert_still_zoned(_server_copy(client, href), tzid, local)

            # 2. An edit that does not touch the time must not move the event.
            fetched = client.get_event(href)
            assert fetched is not None
            fetched.summary = f"{marker} zoned series (renamed)"
            client.update_event(fetched, href)
            renamed = _server_copy(client, href)
            _assert_still_zoned(renamed, tzid, local)
            assert any("RRULE:FREQ=WEEKLY" in line.upper() for line in renamed)

            # 3. Rescheduling must keep the anchoring, only the value may move.
            fetched = client.get_event(href)
            assert fetched is not None
            fetched.start = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)  # 11:00 in Berlin
            client.update_event(fetched, href)
            moved = _server_copy(client, href)
            _assert_still_zoned(moved, tzid, "20260302T110000")
        finally:
            _erase(client, href)


def _erase(client, href: str) -> None:
    """Delete by href and confirm it is gone, retrying: the server is eventual.

    Deleting by href rather than by searching avoids waiting for an index to
    catch up, and the retry covers a DELETE the server accepts but has not yet
    applied to what a GET returns.
    """
    client.delete_event(href)
    for _ in range(10):
        if client._fetch_document(href) is None:
            return
        time.sleep(1)
    raise AssertionError(f"the throwaway event survived deletion: {href}")
