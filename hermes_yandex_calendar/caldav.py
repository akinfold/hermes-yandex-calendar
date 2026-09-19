"""A small CalDAV client for Yandex Calendar (https://caldav.yandex.ru).

Speaks just enough CalDAV (RFC 4791) to discover calendars, run a
time-range ``calendar-query`` REPORT, PUT a new event, and DELETE one.
Untrusted server XML is parsed with :mod:`defusedxml` (never ``xml.etree``).

No Hermes imports — this is unit-testable with ``httpx.MockTransport``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from defusedxml import ElementTree as DET

from .ical import Attendee, Event, build_calendar, parse_events

__all__ = ["CalDAVError", "Calendar", "YandexCalDAVClient", "normalize_email"]

DEFAULT_BASE_URL = "https://caldav.yandex.ru"

_PROPFIND_CALENDARS = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    "<d:prop><d:resourcetype/><d:displayname/></d:prop></d:propfind>"
)

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class CalDAVError(RuntimeError):
    """Raised for transport or protocol failures. The tool layer converts these to JSON."""


def _split_url(url: str):
    if any(ord(char) < 0x21 for char in url):
        raise CalDAVError("CalDAV URLs must not contain control characters or spaces.")
    try:
        return urlsplit(url)
    except ValueError as exc:
        raise CalDAVError("Invalid CalDAV URL.") from exc


def _origin_error(setting: str | None) -> CalDAVError:
    label = setting or "CalDAV URLs"
    return CalDAVError(f"{label} must use a valid HTTPS origin.")


def _https_origin(url: str, *, setting: str | None = None) -> tuple[str, int]:
    """Return a canonical HTTPS origin, rejecting unsafe credential destinations."""
    parsed = _split_url(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise _origin_error(setting) from exc
    invalid = (
        parsed.scheme != "https",
        not parsed.hostname,
        parsed.username is not None,
        bool(parsed.query),
        bool(parsed.fragment),
    )
    if any(invalid):
        raise _origin_error(setting)
    return parsed.hostname, 443 if port is None else port


def _canonical_path(path: str) -> str:
    """Decode once, validate, and return one wire-safe path representation."""
    if _INVALID_PERCENT_ESCAPE.search(path):
        raise CalDAVError("event_href contains an invalid percent escape.")
    segments = _decode_path_segments(path)
    _validate_path_segments(segments)
    return "/".join(quote(segment, safe="@") for segment in segments)


def _decode_path_segments(path: str) -> list[str]:
    try:
        return [unquote(segment, errors="strict") for segment in path.split("/")]
    except UnicodeDecodeError as exc:
        raise CalDAVError("event_href contains an invalid percent escape.") from exc


def _validate_path_segments(segments: list[str]) -> None:
    if any("\\" in segment for segment in segments):
        raise CalDAVError("event_href must not contain a backslash.")
    if any(segment in {".", ".."} for segment in segments):
        raise CalDAVError("event_href must not contain a dot segment.")
    if any(not segment for segment in segments[1:-1]):
        raise CalDAVError("event_href must not contain an empty path segment.")


def _event_path(url: str) -> str:
    """Return the canonical path for an event resource."""
    parsed = _split_url(url)
    if parsed.query or parsed.fragment or not parsed.path:
        raise CalDAVError("event_href must be a CalDAV resource path without query data.")
    path = _canonical_path(parsed.path)
    if path.endswith("/") or not path.rsplit("/", 1)[-1].lower().endswith(".ics"):
        raise CalDAVError("event_href must identify an event resource, not a collection.")
    return path


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


def normalize_email(address: str) -> str:
    """Lowercase an address and fold Yandex's interchangeable mail domains together.

    Public because the live e2e suite compares invitees against what the server
    stored, and must fold domains exactly the way this client does.
    """
    normalized = address.strip().lower()
    local, sep, domain = normalized.rpartition("@")
    if sep and domain in _YANDEX_DOMAIN_ALIASES:
        return f"{local}@yandex.ru"
    return normalized


def _account_email(login: str) -> str:
    login = login.strip()
    return login if "@" in login else f"{login}@yandex.ru"


def _calendar_from_response(response) -> Calendar | None:
    """Build a Calendar from one PROPFIND ``response``, or ``None`` if it isn't one.

    The home collection lists itself and may list other resources; only entries
    whose resourcetype includes ``calendar`` are ours.
    """
    href_el = next(iter(_findall_local(response, "href")), None)
    href = (href_el.text or "").strip() if href_el is not None else ""
    if not href or not any(_local(e.tag) == "calendar" for e in response.iter()):
        return None
    name_el = next(iter(_findall_local(response, "displayname")), None)
    display = (name_el.text or "").strip() if name_el is not None else ""
    return Calendar(href=urlsplit(href).path or href, display_name=display)


def _events_from_response(response) -> list[Event]:
    """The events in one REPORT ``response``, tagged with its href and ETag."""
    data_el = next(iter(_findall_local(response, "calendar-data")), None)
    if data_el is None or not (data_el.text or "").strip():
        return []
    href_el = next(iter(_findall_local(response, "href")), None)
    href = (href_el.text or "").strip() if href_el is not None else ""
    etag_el = next(iter(_findall_local(response, "getetag")), None)
    etag = (etag_el.text or "").strip() if etag_el is not None else ""
    events = parse_events(data_el.text)
    for event in events:
        event.href = urlsplit(href).path or href
        event.etag = etag
    return events


def _calendar_matches(cal: Calendar, ref: str) -> bool:
    """Does ``ref`` name this calendar — by display name or last path segment?

    Hrefs are compared by :meth:`YandexCalDAVClient._same_collection`, which
    canonicalises both sides the way requests are sent.
    """
    needle = ref.strip().lower()
    segments = [p for p in cal.href.split("/") if p]
    return cal.display_name.strip().lower() == needle or (
        bool(segments) and segments[-1].strip().lower() == needle
    )


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
        self._origin = _https_origin(self.base_url, setting="YANDEX_CALENDAR_BASE_URL")
        base = _split_url(self.base_url)
        self._base_path = _canonical_path(base.path).rstrip("/")
        self._origin_url = urlunsplit((base.scheme, base.netloc, "", "", ""))
        self._allowed_raw = list(allowed_calendars or [])
        self._allowed = {c.strip().lower() for c in self._allowed_raw if c.strip()}
        self._owns_client = client is None
        self._client = client or httpx.Client(
            auth=httpx.BasicAuth(login, password),
            headers={"User-Agent": "hermes-yandex-calendar"},
            timeout=timeout,
            # CalDAV home sets are commonly served behind a redirect; httpx does
            # not follow them by default, which would surface as a bare 301.
            follow_redirects=True,
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
        """Resolve an href without ever sending credentials to another origin."""
        parsed = _split_url(href)
        if parsed.scheme or parsed.netloc:
            if _https_origin(href) != self._origin:
                raise CalDAVError("CalDAV URLs must use the configured HTTPS origin.")
            return urlunsplit(
                (
                    parsed.scheme,
                    parsed.netloc,
                    _canonical_path(parsed.path),
                    parsed.query,
                    parsed.fragment,
                )
            )
        if parsed.query or parsed.fragment:
            return (
                self._origin_url
                + _canonical_path(parsed.path)
                + (("?" + parsed.query) if parsed.query else ("#" + parsed.fragment))
            )
        path = "/" + parsed.path.lstrip("/")
        if self._base_path and not (
            path == self._base_path or path.startswith(self._base_path + "/")
        ):
            path = self._base_path + path
        return self._origin_url + _canonical_path(path)

    def _collection_key(self, href: str) -> str:
        return _canonical_path(_split_url(self._url(href)).path).rstrip("/") + "/"

    def _find_calendar(self, calendars: list[Calendar], ref: str) -> Calendar | None:
        """The calendar ``ref`` names — by display name, path segment, or href."""
        return next(
            (
                c
                for c in calendars
                if _calendar_matches(c, ref) or self._same_collection(c.href, ref)
            ),
            None,
        )

    def _same_collection(self, href: str, ref: str) -> bool:
        """Compare two collection references the way the server addresses them.

        Both sides are canonicalised, so ``user%40yandex.ru`` and ``user@yandex.ru``
        name the same calendar. A reference that cannot be canonicalised matches
        nothing.
        """
        try:
            return self._collection_key(href) == self._collection_key(ref)
        except CalDAVError:
            return False

    def _request(self, method: str, href: str, *, content: str | None = None, headers=None):
        try:
            resp = self._client.request(
                method,
                self._url(href),
                content=content.encode("utf-8") if content is not None else None,
                headers=headers,
            )
        # httpx.InvalidURL (e.g. a URL past httpx's length cap) is neither an
        # HTTPError nor a ValueError, so it has to be named here.
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            raise CalDAVError(f"CalDAV request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise CalDAVError(
                "Authentication failed (check YANDEX_CALENDAR_LOGIN / "
                "YANDEX_CALENDAR_APP_PASSWORD; an app password is required)."
            )
        return resp

    def _validate_event_href(self, event_href: str) -> str:
        """Return an event path after origin and configured-calendar checks."""
        if not event_href:
            raise CalDAVError("event_href is required.")
        resolved = self._url(event_href)
        path = _event_path(resolved)
        resolved_parts = _split_url(resolved)
        resolved = urlunsplit((resolved_parts.scheme, resolved_parts.netloc, path, "", ""))
        if not self._allowed:
            return resolved

        parent = path.rsplit("/", 1)[0].rstrip("/") + "/"
        calendars = self.list_calendars()
        if not calendars:
            raise CalDAVError(f"No calendars available matching {self._allowed_raw}.")
        allowed_paths = {self._collection_key(calendar.href) for calendar in calendars}
        if parent not in allowed_paths:
            raise CalDAVError(f"Event {path!r} is not in an allowed calendar.")
        return resolved

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
        parsed = (_calendar_from_response(r) for r in _findall_local(root, "response"))
        return [cal for cal in parsed if cal is not None]

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
        direct = self._href_without_discovery(ref)
        if direct is not None:
            return direct
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
        found = self._find_calendar(calendars, ref)
        if found is not None:
            return found.href
        names = ", ".join(repr(c.display_name or c.href) for c in calendars)
        raise CalDAVError(f"Calendar {ref!r} not found. Available calendars: {names}.")

    def _href_without_discovery(self, ref: str | None) -> str | None:
        """An explicit collection href, usable as-is when no allow-list applies.

        Saves a discovery round-trip; with an allow-list configured the reference
        must still be checked against it, so this returns ``None`` then.
        """
        if not ref or not ref.strip() or self._allowed:
            return None
        candidate = ref.strip()
        if candidate.startswith(("/", "http://", "https://")):
            return urlsplit(candidate).path or candidate
        return None

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
            events.extend(_events_from_response(response))
        return events

    def _fetch_document(self, event_href: str) -> tuple[str, list[Event], str] | None:
        """GET an event resource: its raw text, every VEVENT in it, and its ETag."""
        event_href = self._validate_event_href(event_href)
        resp = self._request("GET", event_href)
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise CalDAVError(f"Fetching event failed: HTTP {resp.status_code}")
        return resp.text, parse_events(resp.text), resp.headers.get("ETag", "").strip()

    def get_event(self, event_href: str) -> Event | None:
        """GET a single event resource and parse it. Returns ``None`` if it is gone.

        Refuses resources holding several VEVENTs (a recurring event plus its
        per-occurrence overrides): this model keeps one VEVENT, so writing it back
        would silently drop the others. :meth:`move_event` copies such resources
        verbatim instead.
        """
        document = self._fetch_document(event_href)
        if document is None:
            return None
        _text, events, etag = document
        if not events:
            return None
        if len(events) > 1:
            raise CalDAVError(
                f"This resource holds {len(events)} components (a recurring event and its "
                "modified occurrences); editing it here would drop them. Change it in the "
                "Yandex Calendar UI, or move/delete the event as a whole."
            )
        event = events[0]
        event.href = urlsplit(event_href).path or event_href
        event.etag = etag
        return event

    # -- write --------------------------------------------------------------

    def _ensure_organizer(self, event: Event) -> None:
        """A meeting with attendees needs an ORGANIZER; default it to the account owner."""
        if event.attendees and event.organizer is None:
            event.organizer = Attendee(email=_account_email(self.login))

    def create_event(self, event: Event, *, calendar: str | None = None) -> Event:
        """PUT a new event. Returns the event with its assigned ``href``/``uid``."""
        if not event.uid:
            event.uid = f"{uuid.uuid4()}@hermes-yandex-calendar"
        self._ensure_organizer(event)
        collection = self.resolve_calendar_href(calendar).rstrip("/") + "/"
        href = f"{collection}{quote(event.uid, safe='')}.ics"
        resp = self._request(
            "PUT",
            href,
            content=build_calendar(event, dtstamp=datetime.now(UTC)),
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"Creating event failed: HTTP {resp.status_code}")
        event.href = _split_url(href).path or href
        return event

    def update_event(self, event: Event, event_href: str) -> Event:
        """PUT a modified event back to its existing resource.

        When the event carries the ``ETag`` it was read with, the write is
        conditional (``If-Match``): if anyone else changed the event meanwhile —
        the Yandex web UI, a phone, another agent — the server refuses the write
        instead of letting it overwrite their change, and the caller is told to
        read the event again. Events built by hand carry no ETag and are written
        unconditionally, as before.
        """
        event_href = self._validate_event_href(event_href)
        self._ensure_organizer(event)
        headers = {"Content-Type": "text/calendar; charset=utf-8"}
        if event.etag:
            headers["If-Match"] = event.etag
        resp = self._request(
            "PUT",
            event_href,
            content=build_calendar(event, dtstamp=datetime.now(UTC)),
            headers=headers,
        )
        if resp.status_code == 412:
            raise CalDAVError(
                "The event changed on the server since it was read, so it was left "
                "untouched. Read it again and reapply the change."
            )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"Updating event failed: HTTP {resp.status_code}")
        event.href = urlsplit(event_href).path or event_href
        # The stored version is stale after our own write; take the new one when
        # the server offers it, otherwise fall back to an unconditional next write.
        event.etag = resp.headers.get("ETag", "").strip()
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
        me = normalize_email(_account_email(self.login))
        mine = next((a for a in event.attendees if normalize_email(a.email) == me), None)
        if mine is None:
            raise CalDAVError(
                f"{self.login} is not listed as an attendee of this event; cannot respond."
            )
        mine.partstat = partstat
        mine.rsvp = False  # a reply has been sent; no further RSVP is expected
        return self.update_event(event, event_href)

    def move_event(self, event_href: str, target_calendar: str) -> Event:
        """Move an event to another calendar (copy into the target, then delete original).

        The resource is copied **byte for byte** rather than re-serialized from the
        parsed model, so everything survives: recurrence rules and their modified
        occurrences, alarms, and any property this parser does not model. Portable
        across CalDAV servers, unlike WebDAV MOVE.
        """
        document = self._fetch_document(event_href)
        if document is None:
            raise CalDAVError(f"Event not found: {event_href}")
        text, events, etag = document
        if not events:
            raise CalDAVError(f"Resource holds no event: {event_href}")
        event = events[0]
        if not event.uid:
            raise CalDAVError("Cannot move an event without a UID.")
        target = self.resolve_calendar_href(target_calendar).rstrip("/") + "/"
        source_collection = event_href.rsplit("/", 1)[0] + "/"
        if self._collection_key(source_collection) == self._collection_key(target):
            event.href = urlsplit(event_href).path or event_href
            return event  # already in the target calendar; nothing to do
        href = self._validate_event_href(f"{target}{quote(event.uid, safe='')}.ics")
        resp = self._request(
            "PUT",
            href,
            content=text,
            headers={"Content-Type": "text/calendar; charset=utf-8", "If-None-Match": "*"},
        )
        if resp.status_code not in (200, 201, 204):
            raise CalDAVError(f"Moving event failed: HTTP {resp.status_code}")
        # The copy exists now, so the original goes only if it is still the version
        # that was copied. Removing a newer one would throw that change away; leaving
        # it means the event exists twice, which the caller can see and undo.
        copied_to = _split_url(href).path or href
        try:
            self.delete_event(event_href, etag=etag)
        except CalDAVError as exc:
            raise CalDAVError(
                f"The event was copied to {copied_to}, but the original at {event_href} could "
                f"not be removed ({exc}) — it is now in both calendars. Delete whichever copy "
                "is not wanted."
            ) from exc
        event.href = copied_to
        return event

    def delete_event(self, event_href: str, *, etag: str = "") -> None:
        """DELETE an event resource by its href (as returned by ``list_events``).

        Given the ``etag`` the event was read with, the delete is conditional
        (``If-Match``): the server refuses it if the event changed meanwhile, so an
        edit made after that read is never removed unnoticed. Without one the delete
        is unconditional, as before.
        """
        event_href = self._validate_event_href(event_href)
        headers = {"If-Match": etag} if etag else None
        resp = self._request("DELETE", event_href, headers=headers)
        if resp.status_code == 412:
            raise CalDAVError(
                "The event changed on the server since it was read, so it was not deleted. "
                "Read it again and delete it if that is still what you want."
            )
        if resp.status_code not in (200, 204, 404):
            raise CalDAVError(f"Deleting event failed: HTTP {resp.status_code}")
