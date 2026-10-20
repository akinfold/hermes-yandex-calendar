"""Untrusted XML: what the CalDAV client refuses, and which Expat it parses with."""

from __future__ import annotations

import codecs
import json
import socket
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import pytest

from hermes_yandex_calendar import caldav, tool
from hermes_yandex_calendar.caldav import (
    MAX_NAMESPACE_URI_CHARS,
    MAX_XML_BYTES,
    MAX_XML_DEPTH,
    MAX_XML_NAMES,
    MIN_EXPAT_VERSION,
    CalDAVError,
    Calendar,
    YandexCalDAVClient,
    _parse_xml,
)
from hermes_yandex_calendar.ical import Event

from .test_caldav import (
    _PERCENT_ENCODED_CALENDARS,
    ETAG_REPORT,
    MULTISTATUS_CALENDARS,
    REPORT_EVENTS,
    SAME_NAME_CALENDARS,
    TWO_CALENDARS,
)

#: What _parse_xml says when it refuses a DTD. Expat's own limits (on entity
#: amplification, for one) fail with other messages, so matching this proves the
#: refusal happened before Expat expanded anything.
REPORT_DTD = "Rejected unsafe calendar-query response: it declares a DTD"
DISCOVERY_DTD = "Rejected unsafe discovery response: it declares a DTD"

NOT_UTF8 = "Rejected calendar-query response: it is not UTF-8"

LONG_NAMESPACE = (
    "Rejected calendar-query response: it declares a namespace URI longer than "
    f"{MAX_NAMESPACE_URI_CHARS} characters"
)

TOO_DEEP = (
    f"Rejected calendar-query response: its elements are nested more than {MAX_XML_DEPTH} deep"
)

#: How the refusal of an oversized listing ends.
SERIES_TOO_LARGE = (
    "; list a shorter time range. A recurring event comes back whole, with all its "
    "modified occurrences, whatever the range: if even one day is refused, a single "
    "event or recurring series in this calendar is larger than the limit"
)

NOT_ASCII_NAMESPACE = "Rejected test document: it declares a namespace URI that is not ASCII"

TOO_MANY_NAMES = (
    f"Rejected test document: it uses more than {MAX_XML_NAMES} distinct element and "
    "attribute names"
)

#: A namespace URI exactly as long as _parse_xml allows.
LONGEST_URI = "urn:" + "u" * (MAX_NAMESPACE_URI_CHARS - 4)

FILE_TEXT = "the contents of a local file"

CALENDAR = "/calendars/user@yandex.ru/events-42/"
WINDOW = (datetime(2026, 7, 25, tzinfo=UTC), datetime(2026, 7, 26, tzinfo=UTC))


def ics(summary: str = "Meeting") -> str:
    return (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:evt-1\r\n"
        f"SUMMARY:{summary}\r\nDTSTART:20260725T140000Z\r\nDTEND:20260725T150000Z\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


def report(dtd: str = "", summary: str = "Meeting") -> bytes:
    """A one-event calendar-query REPORT: *dtd* before the root, *summary* as the title."""
    return (
        f"<?xml version='1.0' encoding='utf-8'?>{dtd}"
        "<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>"
        "<d:response><d:href>/calendars/user@yandex.ru/events-42/evt-1.ics</d:href>"
        '<d:propstat><d:prop><d:getetag>"v1"</d:getetag>'
        f"<c:calendar-data>{ics(summary)}</c:calendar-data></d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    ).encode()


def discovery(dtd: str = "", name: str = "Work") -> bytes:
    """A PROPFIND answer listing one calendar named *name*."""
    return (
        f"<?xml version='1.0' encoding='utf-8'?>{dtd}"
        "<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>"
        f"<d:response><d:href>{CALENDAR}</d:href><d:propstat><d:prop>"
        "<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>"
        f"<d:displayname>{name}</d:displayname></d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
    ).encode()


def _client(raw: bytes, headers: dict[str, str] | None) -> YandexCalDAVClient:
    transport = httpx.MockTransport(lambda _: httpx.Response(207, content=raw, headers=headers))
    return YandexCalDAVClient("user@yandex.ru", "app-pw", client=httpx.Client(transport=transport))


def events_from(raw: bytes, headers: dict[str, str] | None = None) -> list[Event]:
    """List events from a fake server that answers the REPORT with *raw*."""
    with _client(raw, headers) as client:
        return client.list_events(*WINDOW, calendar=CALENDAR)


def calendars_from(raw: bytes, headers: dict[str, str] | None = None) -> list[Calendar]:
    """Discover calendars from a fake server that answers the PROPFIND with *raw*."""
    with _client(raw, headers) as client:
        return client.discover_calendars()


@pytest.fixture
def local_file(tmp_path: Path) -> Path:
    path = tmp_path / "local.txt"
    path.write_text(FILE_TEXT, encoding="utf-8")
    return path


@pytest.fixture
def listener() -> Iterator[socket.socket]:
    """A local port that counts as fetched if anything connects to it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.2)
        yield server


def assert_never_connected(server: socket.socket) -> None:
    with pytest.raises(TimeoutError):
        server.accept()


def url_of(server: socket.socket) -> str:
    host, port = server.getsockname()
    return f"http://{host}:{port}/evil"


# -- the DTD is refused, whatever it declares ---------------------------------


def test_refuses_even_a_harmless_entity() -> None:
    # Expat itself would expand this one: only the parser's own refusal stops it.
    payload = report("<!DOCTYPE d:multistatus [<!ENTITY t 'tea'>]>", "&t;")
    with pytest.raises(CalDAVError, match=REPORT_DTD):
        events_from(payload)


def test_refuses_a_dtd_in_the_discovery_response() -> None:
    payload = discovery("<!DOCTYPE d:multistatus [<!ENTITY t 'tea'>]>", "&t;")
    with pytest.raises(CalDAVError, match=DISCOVERY_DTD):
        calendars_from(payload)


def test_refuses_billion_laughs() -> None:
    entities = "<!ENTITY lol0 'lol'>" + "".join(
        f"<!ENTITY lol{level} '{f'&lol{level - 1};' * 10}'>" for level in range(1, 10)
    )
    payload = report(f"<!DOCTYPE d:multistatus [{entities}]>", "&lol9;")
    with pytest.raises(CalDAVError, match=REPORT_DTD):
        events_from(payload)


def test_refuses_quadratic_blowup() -> None:
    payload = report(f"<!DOCTYPE d:multistatus [<!ENTITY big '{'B' * 50_000}'>]>", "&big;" * 50_000)
    with pytest.raises(CalDAVError, match=REPORT_DTD):
        events_from(payload)


def test_refuses_an_external_file_entity(local_file: Path) -> None:
    payload = report(
        f"<!DOCTYPE d:multistatus [<!ENTITY xxe SYSTEM '{local_file.as_uri()}'>]>", "&xxe;"
    )
    with pytest.raises(CalDAVError, match=REPORT_DTD) as exc_info:
        events_from(payload)
    assert FILE_TEXT not in str(exc_info.value)


def test_refuses_an_external_network_entity(listener: socket.socket) -> None:
    payload = report(
        f"<!DOCTYPE d:multistatus [<!ENTITY xxe SYSTEM '{url_of(listener)}'>]>", "&xxe;"
    )
    with pytest.raises(CalDAVError, match=REPORT_DTD):
        events_from(payload)
    assert_never_connected(listener)


def test_refuses_an_external_dtd(listener: socket.socket, local_file: Path) -> None:
    payload = report(f"<!DOCTYPE d:multistatus SYSTEM '{url_of(listener)}'>", "&xxe;")
    with pytest.raises(CalDAVError, match=REPORT_DTD):
        events_from(payload)
    assert_never_connected(listener)

    payload = report(f"<!DOCTYPE d:multistatus SYSTEM '{local_file.as_uri()}'>")
    with pytest.raises(CalDAVError, match=REPORT_DTD) as exc_info:
        events_from(payload)
    assert FILE_TEXT not in str(exc_info.value)


# -- what is left without a DTD -----------------------------------------------


def test_large_tokens_are_harmless() -> None:
    # One token larger than the 1 MiB chunks CPython feeds Expat: the case of
    # CVE-2023-52425, which Expat before 2.6.0 re-scans once per chunk. Under
    # MAX_XML_BYTES that is at most one re-scan; here a long title and a long
    # attribute value.
    big = "A" * (MAX_XML_BYTES - 1024)
    assert len(big) > 1024 * 1024
    (event,) = events_from(report(summary=big))
    assert event.summary == big
    assert _parse_xml(f"<r a='{big}'/>".encode(), "test document").get("a") == big


def test_refuses_a_report_over_the_size_limit() -> None:
    summary = "A" * (MAX_XML_BYTES - len(report(summary="")))
    (event,) = events_from(report(summary=summary))
    assert event.summary == summary

    # One byte over the limit, with a DTD and a byte that is never UTF-8: only
    # the size is reported, so the size check ran before the UTF-8 check and
    # before Expat saw anything.
    dtd = "<!DOCTYPE d:multistatus [<!ENTITY t 'tea'>]>"
    oversized = report(dtd=dtd, summary=summary[len(dtd) :]) + b"\xff"
    assert len(oversized) == MAX_XML_BYTES + 1
    with pytest.raises(CalDAVError) as exc_info:
        events_from(oversized)
    assert str(exc_info.value) == (
        f"Refusing to parse the calendar-query response: it is {MAX_XML_BYTES + 1} bytes, "
        f"more than the limit of {MAX_XML_BYTES}{SERIES_TOO_LARGE}"
    )


def test_refuses_a_discovery_response_over_the_size_limit() -> None:
    name = "A" * (MAX_XML_BYTES + 1 - len(discovery(name="")))
    oversized = discovery(name=name)
    assert len(oversized) == MAX_XML_BYTES + 1
    with pytest.raises(CalDAVError) as exc_info:
        calendars_from(oversized)
    assert str(exc_info.value) == (
        f"Refusing to parse the discovery response: it is {MAX_XML_BYTES + 1} bytes, "
        f"more than the limit of {MAX_XML_BYTES}"
    )


# -- names and namespaces cost what ElementTree's do ---------------------------


def test_elements_share_one_string_for_each_name() -> None:
    # Each element and attribute in a namespace carries its URI in its name.
    # ElementTree's own parser keeps one string per distinct name; one built
    # afresh per element would hold a copy of the URI for every element.
    elements = "<a:x a:k='1' plain='2'/>" * 3
    root = _parse_xml(f"<r xmlns:a='{LONGEST_URI}'>{elements}</r>".encode(), "test document")
    assert [element.tag for element in root] == ["{" + LONGEST_URI + "}x"] * 3
    assert len({id(element.tag) for element in root}) == 1
    for name in ("{" + LONGEST_URI + "}k", "plain"):
        keys = [next(key for key in element.attrib if key == name) for element in root]
        assert len({id(key) for key in keys}) == 1


@pytest.mark.parametrize(
    "declaration",
    [f"xmlns='{LONGEST_URI}u'", f"xmlns:a='{LONGEST_URI}u'"],
    ids=["default", "prefixed"],
)
def test_refuses_a_namespace_uri_over_the_length_limit(declaration: str) -> None:
    # Declared on an element deep inside, and never used: refused all the same.
    head, tail = report().split(b"<d:getetag>", 1)
    raw = head + f"<d:getetag {declaration}>".encode() + tail
    with pytest.raises(CalDAVError) as exc_info:
        events_from(raw)
    assert str(exc_info.value) == LONG_NAMESPACE


def test_accepts_a_namespace_uri_at_the_length_limit() -> None:
    root = _parse_xml(f"<a:r xmlns:a='{LONGEST_URI}' xmlns='{LONGEST_URI}'/>".encode(), "test")
    assert root.tag == "{" + LONGEST_URI + "}r"


@pytest.mark.parametrize(
    "uri",
    ["urn:caf\u00e9", "urn:\U0001f600"],
    ids=["latin-1", "astral"],
)
def test_refuses_a_namespace_uri_that_is_not_ascii(uri: str) -> None:
    # A name holding a character outside the Basic Multilingual Plane is stored at
    # four bytes per character, so the length limit alone would allow four times
    # the memory; CalDAV's URIs are ASCII.
    with pytest.raises(CalDAVError) as exc_info:
        _parse_xml(f"<r xmlns:a='{uri}'><a:x/></r>".encode(), "test document")
    assert str(exc_info.value) == NOT_ASCII_NAMESPACE


def distinct_names(count: int, uri: str = LONGEST_URI) -> bytes:
    """A document with the root and *count* - 1 elements, each with a new name."""
    elements = "".join(f"<a:n{index}/>" for index in range(count - 1))
    return f"<a:r xmlns:a='{uri}'>{elements}</a:r>".encode()


def test_accepts_distinct_names_up_to_the_limit() -> None:
    root = _parse_xml(distinct_names(MAX_XML_NAMES), "test document")
    assert len(root) == MAX_XML_NAMES - 1


def test_refuses_more_distinct_names_than_the_limit() -> None:
    with pytest.raises(CalDAVError) as exc_info:
        _parse_xml(distinct_names(MAX_XML_NAMES + 1), "test document")
    assert str(exc_info.value) == TOO_MANY_NAMES


def test_attribute_names_count_toward_the_limit() -> None:
    attributes = " ".join(f"a:k{index}='1'" for index in range(MAX_XML_NAMES))
    with pytest.raises(CalDAVError) as exc_info:
        _parse_xml(f"<a:r xmlns:a='{LONGEST_URI}' {attributes}/>".encode(), "test document")
    assert str(exc_info.value) == TOO_MANY_NAMES


def test_a_new_name_for_every_element_costs_little() -> None:
    # Sharing names helps only when they repeat: 2 MiB of elements that each
    # have a new name in the longest namespace allowed would otherwise keep a
    # copy of the URI per element, hundreds of MiB. The name limit stops it early.
    count = (MAX_XML_BYTES - 200) // len("<a:n000000/>")
    raw = distinct_names(count)
    assert len(raw) <= MAX_XML_BYTES
    tracemalloc.start()
    try:
        with pytest.raises(CalDAVError):
            _parse_xml(raw, "test document")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def nested_report(levels: int) -> bytes:
    """report(), with *levels* elements nested one in another inside its ``prop``."""
    head, tail = report().split(b"<d:getetag>", 1)
    return head + b"<x>" * levels + b"</x>" * levels + b"<d:getetag>" + tail


def test_refuses_elements_nested_deeper_than_the_limit() -> None:
    # multistatus, response, propstat and prop hold the nested elements.
    (event,) = events_from(nested_report(MAX_XML_DEPTH - 4))
    assert event.summary == "Meeting"
    with pytest.raises(CalDAVError) as exc_info:
        events_from(nested_report(MAX_XML_DEPTH - 3))
    assert str(exc_info.value) == TOO_DEEP


def nest_response(outer: bytes, old: bytes, new: bytes) -> bytes:
    """*outer*, with a copy of its response, *old* replaced by *new*, inside its own."""
    inner = outer[outer.index(b"<d:response>") : outer.index(b"</d:multistatus>")]
    return outer.replace(b"<d:propstat>", inner.replace(old, new) + b"<d:propstat>", 1)


def test_reads_responses_only_at_the_top_of_the_multistatus() -> None:
    # A response inside another is not one. Read as one, its event would be
    # listed twice: on its own, and as the first calendar-data in the outer one.
    (event,) = events_from(nest_response(report(), b"evt-1.ics", b"evt-2.ics"))
    assert event.href == CALENDAR + "evt-1.ics"
    other = CALENDAR.replace("events-42", "events-43").encode()
    calendars = calendars_from(nest_response(discovery(), CALENDAR.encode(), other))
    assert [calendar.href for calendar in calendars] == [CALENDAR]


#: How Yandex opens a calendar resource: its producer and the zone its times use.
CALENDAR_HEAD = [
    "BEGIN:VCALENDAR",
    "PRODID:-//Yandex LLC//Yandex Calendar//EN",
    "VERSION:2.0",
    "CALSCALE:GREGORIAN",
    "BEGIN:VTIMEZONE",
    "TZID:Europe/Moscow",
    "TZURL:http://tzurl.org/zoneinfo/Europe/Moscow",
    "X-LIC-LOCATION:Europe/Moscow",
    "BEGIN:STANDARD",
    "TZOFFSETFROM:+0400",
    "TZOFFSETTO:+0300",
    "TZNAME:MSK",
    "DTSTART:20141026T020000",
    "RDATE:20141026T020000",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def vevent(
    index: int, *, attendees: int = 5, day: str = "20261021", recurrence: str = ""
) -> list[str]:
    """A meeting on *day*, modelled on how Yandex stores one.

    An alarm, Yandex's URL and category, an organizer, *attendees* attendees and
    a 300-character Russian description. *recurrence* is an RRULE or a
    RECURRENCE-ID line. Not a captured event: an estimate of a typical one.
    """
    return [
        "BEGIN:VEVENT",
        f"DTSTART;TZID=Europe/Moscow:{day}T100000",
        f"DTEND;TZID=Europe/Moscow:{day}T110000",
        "SUMMARY:Еженедельная встреча команды по планированию",
        f"UID:{index:08d}a1b2c3d4e5f6@yandex.ru",
        *([recurrence] if recurrence else []),
        "SEQUENCE:2",
        "DTSTAMP:20261020T090000Z",
        "CREATED:20261001T120000Z",
        "LAST-MODIFIED:20261015T080000Z",
        f"URL:https://calendar.yandex.ru/event?event_id={1_900_000_000 + index}",
        "TRANSP:OPAQUE",
        "CATEGORIES:Мои события",
        "LOCATION:Переговорная 5.12 «Байкал»",
        "DESCRIPTION:" + ("Повестка: статус задач\\, риски\\, планы. " * 8)[:300],
        "ORGANIZER;CN=Иван Петров:mailto:ivan.petrov@yandex.ru",
        *(
            f"ATTENDEE;PARTSTAT=NEEDS-ACTION;CN=Участник {n};ROLE=REQ-PARTICIPANT;"
            f"RSVP=TRUE:mailto:participant{n}@yandex.ru"
            for n in range(attendees)
        ),
        "BEGIN:VALARM",
        "TRIGGER:-PT15M",
        "ACTION:DISPLAY",
        "DESCRIPTION:Reminder",
        "END:VALARM",
        "END:VEVENT",
    ]


def entry(index: int, lines: list[str]) -> str:
    """One REPORT entry carrying *lines*, folded and escaped as CalDAV sends them."""
    text = "".join(_fold(line) + "\r\n" for line in lines)
    escaped = text.replace("&", "&amp;").replace("<", "&lt;")
    return (
        f"<d:response><d:href>{CALENDAR}{index:08d}a1b2c3d4e5f6%40yandex.ru.ics</d:href>"
        f'<d:propstat><d:prop><d:getetag>"{1_760_000_000_000 + index}"</d:getetag>'
        f"<c:calendar-data>{escaped}</c:calendar-data></d:prop>"
        "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    )


def meeting(index: int) -> str:
    """One REPORT entry for a meeting with five attendees, in a resource of its own."""
    return entry(index, [*CALENDAR_HEAD, *vevent(index), "END:VCALENDAR"])


def series(overrides: int, *, attendees: int = 15) -> str:
    """One REPORT entry for a weekday series and *overrides* of its occurrences.

    Yandex keeps a series and every modified occurrence of it in one resource,
    and a calendar-query returns that resource whole, whatever the range.
    """
    days = [date(2025, 1, 1) + timedelta(days=n) for n in range(overrides)]
    rule = "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    modified = (
        line
        for day in days
        for line in vevent(
            0,
            attendees=attendees,
            day=f"{day:%Y%m%d}",
            recurrence=f"RECURRENCE-ID;TZID=Europe/Moscow:{day:%Y%m%d}T100000",
        )
    )
    return entry(
        0,
        [
            *CALENDAR_HEAD,
            *vevent(0, attendees=attendees, recurrence=rule),
            *modified,
            "END:VCALENDAR",
        ],
    )


def listing(*entries: str) -> bytes:
    """A calendar-query REPORT holding *entries*."""
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        "<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>"
        + "".join(entries)
        + "</d:multistatus>"
    ).encode()


def _fold(line: str) -> str:
    """Fold a content line at 75 octets, never inside a UTF-8 character."""
    raw = line.encode()
    parts = []
    while len(raw) > 75:
        cut = 75
        while raw[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(raw[:cut].decode())
        raw = b" " + raw[cut:]
    parts.append(raw.decode())
    return "\r\n".join(parts)


#: Ten meetings on each of 22 working days: a busy month.
BUSY_MONTH = 22 * 10


def test_the_size_limit_holds_a_busy_month_with_room_to_spare() -> None:
    per_meeting = len(meeting(0).encode())
    # The figures the comment on MAX_XML_BYTES gives: about 2.5 KB a meeting,
    # about 550 KB for a busy month, over 800 meetings under the cap.
    assert 2_300 < per_meeting < 2_700
    assert 500_000 < BUSY_MONTH * per_meeting < 600_000
    # Much less margin risks refusing a real month; a much higher limit lets a
    # hostile response cost more time and memory.
    assert 3 * BUSY_MONTH * per_meeting <= MAX_XML_BYTES
    assert MAX_XML_BYTES <= 4 * 1024 * 1024

    count = (MAX_XML_BYTES - len(listing())) // per_meeting
    raw = listing(*(meeting(i) for i in range(count)))
    assert len(raw) <= MAX_XML_BYTES
    events = events_from(raw)
    assert len(events) == count > 800
    assert events[0].summary == "Еженедельная встреча команды по планированию"
    assert len(events[0].attendees) == 5


def test_a_recurring_series_counts_in_full_with_every_modified_occurrence() -> None:
    # The figures the comment on MAX_XML_BYTES and the README give: a series of
    # fifteen-attendee meetings passes the limit on its own at about 700
    # modified occurrences, which no shorter range avoids.
    per_override = len(listing(series(1))) - len(listing(series(0)))
    fit = (MAX_XML_BYTES - len(listing(series(0)))) // per_override
    assert 2_700 < per_override < 3_100
    assert 650 < fit < 750

    events = events_from(listing(series(fit)))
    assert len(events) == fit + 1
    assert len({event.href for event in events}) == 1
    with pytest.raises(CalDAVError) as exc_info:
        events_from(listing(series(fit + 1)))
    assert str(exc_info.value).endswith(SERIES_TOO_LARGE)


# -- UTF-8 only ----------------------------------------------------------------

#: A lone UTF-16 high surrogate followed by "A": Expat before 2.8.5 decodes the
#: pair as U+10041, a character the document never contained (CVE-2026-93990).
LONE_SURROGATE = (
    codecs.BOM_UTF16_LE
    + "<?xml version='1.0' encoding='utf-16'?><y>".encode("utf-16-le")
    + b"\x00\xd8"
    + "A</y>".encode("utf-16-le")
)


@pytest.mark.parametrize(
    "raw",
    [
        LONE_SURROGATE,
        codecs.BOM_UTF16_LE + "<r>x</r>".encode("utf-16-le"),
        codecs.BOM_UTF16_BE + "<r>x</r>".encode("utf-16-be"),
        "<r>x</r>".encode("utf-16-le"),
        "<r>x</r>".encode("utf-16-be"),
        b"<?xml version='1.0' encoding='windows-1252'?><r>\x80</r>",
        b"<r>a\x00b</r>",
    ],
    ids=[
        "lone-surrogate",
        "utf-16le-bom",
        "utf-16be-bom",
        "utf-16le",
        "utf-16be",
        "windows-1252",
        "nul",
    ],
)
def test_refuses_a_document_that_is_not_utf8(raw: bytes) -> None:
    with pytest.raises(CalDAVError, match=NOT_UTF8):
        events_from(raw)


@pytest.mark.parametrize("declared", ["windows-1252", "iso-8859-1", "utf-16", "x-unknown"])
def test_reads_utf8_whatever_encoding_the_document_declares(declared: str) -> None:
    raw = f"<?xml version='1.0' encoding='{declared}'?><r>€ж</r>".encode()
    assert _parse_xml(raw, "test document").text == "€ж"


def test_reads_the_body_as_utf8_whatever_charset_the_header_declares() -> None:
    # httpx would decode the body as Latin-1 here; the bytes are what Expat reads.
    latin1 = {"Content-Type": "application/xml; charset=iso-8859-1"}
    (event,) = events_from(report(summary="Планёрка"), latin1)
    assert event.summary == "Планёрка"
    (calendar,) = calendars_from(discovery(name="Работа"), latin1)
    assert calendar.display_name == "Работа"


def test_refuses_another_charset_even_when_the_header_declares_it() -> None:
    raw = report(summary="Планёрка").decode().replace("utf-8", "windows-1251").encode("cp1251")
    with pytest.raises(CalDAVError, match=NOT_UTF8):
        events_from(raw, {"Content-Type": "application/xml; charset=windows-1251"})


def test_accepts_a_utf8_byte_order_mark() -> None:
    (event,) = events_from(codecs.BOM_UTF8 + report())
    assert event.summary == "Meeting"


def test_an_undeclared_entity_is_a_parse_error() -> None:
    with pytest.raises(CalDAVError, match="Could not parse calendar-query response: undefined"):
        events_from(report(summary="&xxe;"))


# -- the same tree ElementTree builds ------------------------------------------


def shape(element: ET.Element) -> tuple:
    return (
        element.tag,
        element.attrib,
        element.text,
        element.tail,
        [shape(child) for child in element],
    )


#: calendar-data wrapped in CDATA, as some servers send it.
CDATA_REPORT = REPORT_EVENTS.replace(
    "<c:calendar-data>BEGIN", "<c:calendar-data><![CDATA[BEGIN", 1
).replace("END:VCALENDAR</c:calendar-data>", "END:VCALENDAR]]></c:calendar-data>", 1)

#: DAV: as the default namespace, CalDAV under an upper-case prefix, CRLF line
#: endings and escaped markup in the event text, a comment and a processing
#: instruction.
DEFAULT_NAMESPACE_REPORT = (
    "<?xml version='1.0' encoding='UTF-8'?>\r\n"
    "<multistatus xmlns='DAV:' xmlns:C='urn:ietf:params:xml:ns:caldav'>\r\n"
    "<!-- served by a test --><?test ignored?>"
    "<response><href>/calendars/user%40yandex.ru/events-42/evt-2.ics</href>"
    '<propstat><prop><getetag>"e2"</getetag><C:calendar-data>BEGIN:VCALENDAR\r\n'
    "BEGIN:VEVENT\r\nUID:evt-2\r\nSUMMARY:Q&amp;A &lt;draft&gt;\r\n"
    "DTSTART:20260725T140000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n</C:calendar-data>"
    "</prop><status>HTTP/1.1 200 OK</status></propstat></response></multistatus>"
)

EMPTY_MULTISTATUS = "<?xml version='1.0'?><d:multistatus xmlns:d='DAV:'></d:multistatus>"

#: The default namespace undeclared on a child: Expat reports that URI as None.
UNDECLARED_DEFAULT = "<multistatus xmlns='DAV:'><x xmlns=''><y/></x><response/></multistatus>"


@pytest.mark.parametrize(
    "text",
    [
        MULTISTATUS_CALENDARS,
        REPORT_EVENTS,
        SAME_NAME_CALENDARS,
        TWO_CALENDARS,
        _PERCENT_ENCODED_CALENDARS,
        ETAG_REPORT,
        CDATA_REPORT,
        DEFAULT_NAMESPACE_REPORT,
        EMPTY_MULTISTATUS,
        UNDECLARED_DEFAULT,
        report().decode(),
        discovery().decode(),
        "<d:multistatus xmlns:d='DAV:' xmlns:c='urn:ietf:params:xml:ns:caldav'>"
        + meeting(7)
        + "</d:multistatus>",
    ],
)
def test_builds_the_tree_elementtree_builds(text: str) -> None:
    # The trusted fixtures of this suite, so ElementTree can serve as the oracle.
    expected = ET.fromstring(text)  # nosec B314
    assert shape(_parse_xml(text.encode(), "test document")) == shape(expected)


def test_has_expat_buffer_character_data(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unbuffered, Expat calls back once per line and per character reference:
    # 2 MiB of newlines took five times as long and four times the memory.
    created = []
    create = caldav.expat.ParserCreate

    def remember(*args: object, **kwargs: object):
        created.append(create(*args, **kwargs))
        return created[-1]

    monkeypatch.setattr(caldav.expat, "ParserCreate", remember)
    (event,) = events_from(DEFAULT_NAMESPACE_REPORT.encode())
    assert event.summary == "Q&A <draft>"
    assert [parser.buffer_text for parser in created] == [True]


def test_builds_the_tree_elementtree_would_for_namespaced_attributes() -> None:
    root = _parse_xml(
        b"<?xml version='1.0' encoding='utf-8'?>"
        b"<y:a xmlns:y='urn:y' xmlns='urn:d' y:id='1' plain='2'>"
        b"<b>\xe2\x82\xac <!-- dropped --><?dropped too?>&amp;<![CDATA[<x>]]></b>tail</y:a>",
        "test document",
    )
    assert root.tag == "{urn:y}a"
    assert root.attrib == {"{urn:y}id": "1", "plain": "2"}
    (child,) = list(root)
    assert child.tag == "{urn:d}b"
    assert child.text == "€ &<x>"
    assert child.tail == "tail"


def test_reads_the_same_events_and_calendars_from_caldav_shaped_responses() -> None:
    (cdata,) = events_from(CDATA_REPORT.encode())
    assert (cdata.uid, cdata.summary) == ("evt-1", "Meeting")
    (crlf,) = events_from(DEFAULT_NAMESPACE_REPORT.encode())
    assert (crlf.uid, crlf.summary, crlf.etag) == ("evt-2", "Q&A <draft>", '"e2"')
    assert crlf.href == "/calendars/user%40yandex.ru/events-42/evt-2.ics"
    assert [c.display_name for c in calendars_from(TWO_CALENDARS.encode())] == [
        "Work",
        "Personal",
    ]


def test_a_malformed_document_fails_with_expats_own_message() -> None:
    with pytest.raises(ET.ParseError) as expected:
        # A literal of this suite, parsed only for the message ElementTree gives.
        ET.fromstring("<<not xml")  # nosec B314
    with pytest.raises(CalDAVError) as exc_info:
        calendars_from(b"<<not xml")
    assert str(exc_info.value) == f"Could not parse discovery response: {expected.value}"


# -- which Expat it parses with -----------------------------------------------


@pytest.mark.parametrize("version", [(2, 4, 4), (2, 3, 99), (1, 95, 8)])
def test_refuses_to_parse_on_an_old_expat(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int]
) -> None:
    monkeypatch.setattr(caldav.expat, "version_info", version)
    found = ".".join(map(str, version))
    with pytest.raises(CalDAVError) as exc_info:
        events_from(report())
    message = str(exc_info.value)
    assert message.startswith("Refusing to parse the calendar-query response: ")
    assert f"this Python uses Expat {found}, older than 2.4.5, the oldest" in message
    assert message.endswith("See https://github.com/akinfold/hermes-yandex-calendar#requirements")


def test_the_requirements_link_names_a_readme_section() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert caldav.REQUIREMENTS_URL.endswith("#requirements")
    assert "\n## Requirements\n" in readme


# 2.4.7: Ubuntu 22.04's libexpat; 2.5.0: Debian 12's and RHEL 9's. 2.10.0 also
# catches a version compared as text.
@pytest.mark.parametrize(
    "version", [MIN_EXPAT_VERSION, (2, 4, 7), (2, 5, 0), (2, 6, 0), (2, 10, 0), (3, 0, 0)]
)
def test_parses_on_a_supported_expat(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int]
) -> None:
    monkeypatch.setattr(caldav.expat, "version_info", version)
    assert [c.display_name for c in calendars_from(TWO_CALENDARS.encode())] == [
        "Work",
        "Personal",
    ]
    assert [e.summary for e in events_from(report())] == ["Meeting"]


def test_readme_names_the_same_minimum_and_size_limit() -> None:
    minimum = ".".join(map(str, MIN_EXPAT_VERSION))
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    text = " ".join(readme.split())
    assert f"Expat {minimum} or newer" in text
    assert f"an XML document larger than {MAX_XML_BYTES // (1024 * 1024)} MiB" in text
    assert f"namespace URI longer than {MAX_NAMESPACE_URI_CHARS} characters" in text
    assert f"nests its elements more than {MAX_XML_DEPTH} deep" in text
    assert f"uses more than {MAX_XML_NAMES} distinct element and attribute names" in text


def test_this_python_has_a_supported_expat() -> None:
    # The suite parses for real; on an older Expat every parsing test above
    # would fail for that reason alone, so say it plainly here.
    assert tuple(caldav.expat.version_info) >= MIN_EXPAT_VERSION, caldav.expat.EXPAT_VERSION


# -- every refusal reaches the agent as an ordinary error ---------------------


@pytest.mark.parametrize(
    ("raw", "expat_version", "error"),
    [
        (report("<!DOCTYPE d:multistatus [<!ENTITY e 'x'>]>"), None, REPORT_DTD),
        (report(), (2, 4, 4), "this Python uses Expat 2.4.4, older than 2.4.5"),
        (report().replace(b"Meeting", b"M" * MAX_XML_BYTES), None, "list a shorter time range"),
        ("<r>x</r>".encode("utf-16"), None, NOT_UTF8),
        (f"<r xmlns='{LONGEST_URI}u'/>".encode(), None, LONG_NAMESPACE),
        (nested_report(MAX_XML_DEPTH), None, TOO_DEEP),
    ],
    ids=["dtd", "old-expat", "too-large", "utf-16", "long-namespace", "too-deep"],
)
def test_list_events_reports_xml_it_will_not_parse(
    monkeypatch: pytest.MonkeyPatch,
    raw: bytes,
    expat_version: tuple[int, int, int] | None,
    error: str,
) -> None:
    monkeypatch.setattr(tool, "build_client", lambda: _client(raw, None))
    if expat_version:
        monkeypatch.setattr(caldav.expat, "version_info", expat_version)
    out = json.loads(tool.handle_list({"calendar": CALENDAR}))
    assert set(out) == {"error"}
    assert error in out["error"]


def test_list_calendars_reports_a_dtd(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = discovery("<!DOCTYPE d:multistatus [<!ENTITY e 'x'>]>")
    monkeypatch.setattr(tool, "build_client", lambda: _client(raw, None))
    assert json.loads(tool.handle_list_calendars({})) == {"error": DISCOVERY_DTD}
