"""A small CalDAV client for Yandex Calendar (https://caldav.yandex.ru).

Speaks just enough CalDAV (RFC 4791) to discover calendars, run a
time-range ``calendar-query`` REPORT, PUT a new event, and DELETE one.
Untrusted server XML is parsed with :mod:`defusedxml` (never ``xml.etree``).

No Hermes imports — this is unit-testable with ``httpx.MockTransport``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote, urlsplit

import httpx
from defusedxml import ElementTree as DET

from .ical import Attendee, Event, build_calendar, parse_events

__all__ = ["CalDAVError", "Calendar", "YandexCalDAVClient"]

DEFAULT_BASE_URL = "https://caldav.yandex.ru"

_PROPFIND_CALENDARS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>"
)


class CalDAVError(RuntimeError):
    """Raised for transport or protocol failures. The tool layer converts these to JSON."""


@dataclass
class Calendar:
    href: str
    display_name: str = ""


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix from an ElementTree tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _findall_local(element, name: str) -> list:
    return [e for e in element.iter() if _local(e.tag) == name]


# Yandex delivers all of these to the same mailbox and rewrites addresses to the
# canonical @yandex.ru form, so an event created with an @ya.ru ORGANIZER/ATTENDEE
# comes back as @yandex.ru. Compare mailboxes with the domain folded to one name.
_YANDEX_DOMAIN_ALIASES = frozenset(
    {"ya.ru", "yandex.ru", "yandex.com", "yandex.by", "yandex.kz", "yandex.ua", "narod.ru"}
)


def _normalize_email(address: str) -> str:
    """Lowercase an address and fold Yandex's interchangeable mail domains together."""
    normalized = address.strip().lower()
    local, sep, domain = normalized.rpartition("@")
    if sep and domain in _YANDEX_DOMAIN_ALIASES:
        return f"{local}@yandex.ru"
    return normalized


def _fmt_utc(value: datetime | date) -> str:
    if isinstance(value, datetime):
        dt = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return dt.strftime("%Y%m%dT%H%M%SZ")


class YandexCalDAVClient:
    """CalDAV client bound to one Yandex account.

    Pass a preconfigured ``client`` (with ``httpx.MockTransport``) in tests;
    otherwise one is built from the credentials with Basic auth.
    """

    def __init__(
        self,
        login: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        allowed_calendars: list[str] | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.login = login
        self.base_url = base_url.rstrip("/")
        self._allowed_raw = list(allowed_calendars or [])
        self._allowed = {c.strip().lower() for c in self._allowed_raw if c.strip()}
        self._owns_client = client is None
        self._client = client or httpx.Client(
            auth=httpx.BasicAuth(login, password),
            headers={"User-Agent": "hermes-yandex-calendar"},
            timeout=timeout,
        )
        self._calendars: list[Calendar] | None = None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> YandexCalDAVClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- helpers ------------------------------------------------------------

    def _url(self, href: str) -> str:
        """Resolve a href (absolute path or full URL) against the base URL."""
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if not href.startswith("/"):
            href = "/" + href
        return self.base_url + href

    def _request(self, method: str, href: str, *, content: str | None = None, headers=None):
        try:
            resp = self._client.request(
                method,
                self._url(href),
                content=content.encode("utf-8") if content is not None else None,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise CalDAVError(f"CalDAV request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise CalDAVError(
                "Authentication failed (check YANDEX_CALENDAR_LOGIN / "
                "YANDEX_CALENDAR_APP_PASSWORD; an app password is required)."
            )
        return resp

    # -- discovery ----------------------------------------------------------

    def discover_calendars(self) -> list[Calendar]:
        """Enumerate calendar collections under the account's home set."""
        home = f"/calendars/{quote(self.login)}/"
        resp = self._request(
            "PROPFIND",
            home,
            content=_PROPFIND_CALENDARS,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        if resp.status_code >= 400:
            raise CalDAVError(f"Calendar discovery failed: HTTP {resp.status_code}")
        return self._parse_calendars(resp.text, home)

    def _parse_calendars(self, xml_text: str, home: str) -> list[Calendar]:
        try:
            root = DET.fromstring(xml_text)
        except Exception as exc:
            raise CalDAVError(f"Could not parse discovery response: {exc}") from exc
        calendars: list[Calendar] = []
        for response in _findall_local(root, "response"):
            href_el = next(iter(_findall_local(response, "href")), None)
            if href_el is None or not (href_el.text or "").strip():
                continue
            href = href_el.text.strip()
            # the home collection lists itself; skip non-calendar resources
            is_calendar = any(_local(e.tag) == "calendar" for e in response.iter())
            if not is_calendar:
                continue
            name_el = next(iter(_findall_local(response, "displayname")), None)
            display = (name_el.text or "").strip() if name_el is not None else ""
            calendars.append(Calendar(href=urlsplit(href).path or href, display_name=display))
        return calendars

    def list_calendars(self) -> list[Calendar]:
        """Calendars this client may use — all discovered, filtered by the allow-list."""
        if self._calendars is None:
            self._calendars = self.discover_calendars()
        if not self._allowed:
            return list(self._calendars)
        return [c for c in self._calendars if self._calendar_allowed(c)]

    def _calendar_allowed(self, cal: Calendar) -> bool:
        keys = set()
        if cal.display_name:
            keys.add(cal.display_name.strip().lower())
        seg = [p for p in cal.href.split("/") if p]
        if seg:
            keys.add(seg[-1].strip().lower())
        return bool(keys & self._allowed)

    def resolve_calendar_href(self, ref: str | None = None) -> str:
        """Resolve a calendar name / last-path-segment / href to a usable href.

        ``None`` selects the default calendar (the first allowed one). An explicit
        ``ref`` must match an allowed calendar, otherwise a ``CalDAVError`` naming
        the available calendars is raised.
        """
        # Fast path: an explicit collection href with no allow-list to enforce —
        # use it directly instead of a discovery round-trip.
        if ref and ref.strip() and not self._allowed:
            r = ref.strip()
            if r.startswith(("/", "http://", "https://")):
                return urlsplit(r).path or r
        calendars = self.list_calendars()
        if not calendars:
            hint = (
                f" matching the configured list {self._allowed_raw}"
                if self._allowed
                else " for this Yandex account"
            )
            raise CalDAVError(f"No calendars available{hint}.")
        if ref is None or not ref.strip():
            return calendars[0].href
        needle = ref.strip().lower()
        ref_path = urlsplit(ref).path.rstrip("/")
        for cal in calendars:
            seg = [p for p in cal.href.split("/") if p]
            if (
                cal.display_name.strip().lower() == needle
                or (seg and seg[-1].strip().lower() == needle)
                or cal.href.rstrip("/") == ref_path
            ):
                return cal.href
        names = ", ".join(repr(c.display_name or c.href) for c in calendars)
        raise CalDAVError(f"Calendar {ref!r} not found. Available calendars: {names}.")

    def default_calendar_href(self) -> str:
        """The href of the calendar writes/queries target when none is given."""
        return self.resolve_calendar_href(None)

    # -- read ---------------------------------------------------------------

    def list_events(
        self,
        start: datetime | date,
        end: datetime | date,
        *,
        calendar: str | None = None,
    ) -> list[Event]:
        """Return VEVENTs overlapping [start, end) in the target calendar.

        ``calendar`` is a calendar name / path segment / href; ``None`` uses the
        default calendar.
        """
        href = self.resolve_calendar_href(calendar)
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            "<d:prop><d:getetag/><c:calendar-data/></d:prop>"
            '<c:filter><c:comp-filter name="VCALENDAR">'
            '<c:comp-filter name="VEVENT">'
            f'<c:time-range start="{_fmt_utc(start)}" end="{_fmt_utc(end)}"/>'
            "</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>"
        )
        resp = self._request(
            "REPORT",
            href,
            content=body,
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        if resp.status_code >= 400:
            raise CalDAVError(f"Listing events failed: HTTP {resp.status_code}")
        return self._parse_report(resp.text)

    def _parse_report(self, xml_text: str) -> list[Event]:
        try:
            root = DET.fromstring(xml_text)
        except Exception as exc:
            raise CalDAVError(f"Could not parse calendar-query response: {exc}") from exc
        events: list[Event] = []
        for response in _findall_local(root, "response"):
            href_el = next(iter(_findall_local(response, "href")), None)
            href = (href_el.text or "").strip() if href_el is not None else ""
            data_el = next(iter(_findall_local(response, "calendar-data")), None)
            if data_el is None or not (data_el.text or "").strip():
                continue
            for event in parse_events(data_el.text):
                event.href = urlsplit(href).path or href
                events.append(event)
        return events

    def get_event(self, event_href: str) -> Event | None:
        """GET a single event resource and parse it. Returns ``None`` if it is gone."""
        if not event_href:
            raise CalDAVError("event_href is required.")
        resp = self._request("GET", event_href)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise CalDAVError(f"Fetching event failed: HTTP {resp.status_code}")
        events = parse_events(resp.text)
        if not events:
            return None
        event = events[0]
        event.href = urlsplit(event_href).path or event_href
        return event

    # -- write --------------------------------------------------------------

    def _ensure_organizer(self, event: Event) -> None:
        """A meeting with attendees needs an ORGANIZER; default it to the account owner."""
        if event.attendees and event.organizer is None:
            event.organizer = Attendee(email=self.login)

    def create_event(self, event: Event, *, calendar: str | None = None) -> Event:
        """PUT a new event. Returns the event with its assigned ``href``/``uid``."""
        if not event.uid:
            event.uid = f"{uuid.uuid4()}@hermes-yandex-calendar"
        self._ensure_organizer(event)
        collection = self.resolve_calendar_href(calendar).rstrip("/") + "/"
        href = f"{collection}{quote(event.uid)}.ics"
        resp = self._request(
            "PUT",
            href,
            content=build_calendar(event, dtstamp=datetime.now(UTC)),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"Creating event failed: HTTP {resp.status_code}")
        event.href = href
        return event

    def update_event(self, event: Event, event_href: str) -> Event:
        """PUT a modified event back to its existing resource (overwrites in place)."""
        if not event_href:
            raise CalDAVError("event_href is required to update an event.")
        self._ensure_organizer(event)
        resp = self._request(
            "PUT",
            event_href,
            content=build_calendar(event, dtstamp=datetime.now(UTC)),
            headers={"Content-Type": "text/calendar; charset=utf-8"},
        )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"Updating event failed: HTTP {resp.status_code}")
        event.href = urlsplit(event_href).path or event_href
        return event

    def respond_to_event(self, event_href: str, partstat: str) -> Event:
        """Set the account owner's participation status (ACCEPTED/DECLINED/TENTATIVE).

        Flips the PARTSTAT on the ATTENDEE entry matching the logged-in address and
        PUTs the event back, which the server processes as the invitation reply.
        Matching ignores case and Yandex's interchangeable mail domains, since the
        server rewrites e.g. ``@ya.ru`` addresses to their ``@yandex.ru`` form.
        """
        event = self.get_event(event_href)
        if event is None:
            raise CalDAVError(f"Event not found: {event_href}")
        me = _normalize_email(self.login)
        mine = next((a for a in event.attendees if _normalize_email(a.email) == me), None)
        if mine is None:
            raise CalDAVError(
                f"{self.login} is not listed as an attendee of this event; cannot respond."
            )
        mine.partstat = partstat
        mine.rsvp = False  # a reply has been sent; no further RSVP is expected
        return self.update_event(event, event_href)

    def move_event(self, event_href: str, target_calendar: str) -> Event:
        """Move an event to another calendar (copy into the target, then delete original).

        Portable across CalDAV servers and preserves everything the event carries
        (recurrence, alarms, attendees), since the resource is round-tripped whole.
        """
        event = self.get_event(event_href)
        if event is None:
            raise CalDAVError(f"Event not found: {event_href}")
        target = self.resolve_calendar_href(target_calendar).rstrip("/") + "/"
        source_collection = event_href.rsplit("/", 1)[0] + "/"
        if urlsplit(source_collection).path == urlsplit(target).path:
            event.href = urlsplit(event_href).path or event_href
            return event  # already in the target calendar; nothing to do
        moved = self.create_event(event, calendar=target)  # PUT into target first
        self.delete_event(event_href)  # only remove the original once the copy exists
        return moved

    def delete_event(self, event_href: str) -> None:
        """DELETE an event resource by its href (as returned by ``list_events``)."""
        if not event_href:
            raise CalDAVError("event_href is required to delete an event.")
        resp = self._request("DELETE", event_href)
        if resp.status_code not in (200, 204, 404):
            raise CalDAVError(f"Deleting event failed: HTTP {resp.status_code}")
