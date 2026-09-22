"""Unit tests for the iCalendar parse/serialize helpers (no network)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from hermes_yandex_calendar.ical import Attendee, Event, build_calendar, parse_events


def test_parse_utc_event():
    text = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:abc-123\r\n"
        "SUMMARY:Standup\r\n"
        "DTSTART:20260725T090000Z\r\n"
        "DTEND:20260725T093000Z\r\n"
        "LOCATION:Room 1\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    (event,) = parse_events(text)
    assert event.uid == "abc-123"
    assert event.summary == "Standup"
    assert event.location == "Room 1"
    assert event.start == datetime(2026, 7, 25, 9, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 7, 25, 9, 30, tzinfo=UTC)
    assert event.all_day is False


def test_parse_all_day_event():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:day-1\r\n"
        "SUMMARY:Holiday\r\n"
        "DTSTART;VALUE=DATE:20260101\r\n"
        "DTEND;VALUE=DATE:20260102\r\n"
        "END:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    assert event.all_day is True
    assert event.start == date(2026, 1, 1)
    assert event.end == date(2026, 1, 2)


def test_parse_tzid_event():
    text = (
        "BEGIN:VEVENT\r\nUID:tz-1\r\nDTSTART;TZID=Europe/Moscow:20260725T120000\r\nEND:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    assert isinstance(event.start, datetime)
    # Moscow is UTC+3, so noon local == 09:00 UTC
    assert event.start.astimezone(UTC).hour == 9


def test_unfolding_and_escaping():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:fold-1\r\n"
        "DESCRIPTION:line one\\nline two\\; still here and fol\r\n"
        " ded tail\r\n"
        "END:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    # RFC 5545 unfolding removes CRLF + one leading space and concatenates directly
    # (no space inserted) — the word split across the fold is rejoined.
    assert event.description == "line one\nline two; still here and folded tail"


def test_multiple_events():
    text = "BEGIN:VEVENT\r\nUID:a\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:b\r\nEND:VEVENT\r\n"
    events = parse_events(text)
    assert [e.uid for e in events] == ["a", "b"]


def test_build_roundtrip():
    event = Event(
        uid="rt-1",
        summary="Lunch, with a comma",
        start=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
        end=datetime(2026, 7, 25, 13, 0, tzinfo=UTC),
        location="Cafe",
        description="notes; here",
    )
    ics = build_calendar(event)
    assert "BEGIN:VCALENDAR" in ics and ics.endswith("\r\n")
    assert "SUMMARY:Lunch\\, with a comma" in ics
    assert "DTSTART:20260725T120000Z" in ics
    (parsed,) = parse_events(ics)
    assert parsed.summary == "Lunch, with a comma"
    assert parsed.description == "notes; here"
    assert parsed.start == event.start


def test_build_all_day():
    event = Event(
        uid="ad-1", summary="Trip", start=date(2026, 5, 1), end=date(2026, 5, 3), all_day=True
    )
    ics = build_calendar(event)
    assert "DTSTART;VALUE=DATE:20260501" in ics
    assert "DTEND;VALUE=DATE:20260503" in ics


def test_build_folds_long_lines():
    event = Event(uid="long-1", summary="x" * 200)
    ics = build_calendar(event)
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_naive_datetime_has_no_z():
    event = Event(uid="naive-1", start=datetime(2026, 7, 25, 8, 0))
    ics = build_calendar(event)
    assert "DTSTART:20260725T080000\r\n" in ics


def test_parse_attendees_organizer_transp():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:m-1\r\n"
        "SUMMARY:Sync\r\n"
        "TRANSP:TRANSPARENT\r\n"
        "ORGANIZER;CN=Boss:mailto:boss@yandex.ru\r\n"
        "ATTENDEE;CN=Ann;ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=TRUE:mailto:ann@x.ru\r\n"
        "ATTENDEE:mailto:bob@x.ru\r\n"
        "END:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    assert event.transp == "TRANSPARENT"
    assert event.organizer.email == "boss@yandex.ru"
    assert event.organizer.name == "Boss"
    assert [a.email for a in event.attendees] == ["ann@x.ru", "bob@x.ru"]
    ann = event.attendees[0]
    assert ann.name == "Ann" and ann.partstat == "ACCEPTED" and ann.rsvp is True
    assert event.attendees[1].rsvp is None


def test_parse_quoted_calendar_address_parameters():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:quoted\r\n"
        'ATTENDEE;CN="Alice; VP: Sales";PARTSTAT=NEEDS-ACTION:mailto:alice@x.ru\r\n'
        "END:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    assert event.attendees[0].name == "Alice; VP: Sales"
    assert event.attendees[0].partstat == "NEEDS-ACTION"


def test_build_attendees_roundtrip():
    event = Event(
        uid="m-2",
        summary="Plan",
        organizer=Attendee(email="me@yandex.ru", name="Me"),
        attendees=[Attendee(email="a@x.ru", name="A, B", role="REQ-PARTICIPANT", rsvp=True)],
        transp="OPAQUE",
    )
    ics = build_calendar(event)
    assert "TRANSP:OPAQUE" in ics
    assert "ORGANIZER;CN=Me:mailto:me@yandex.ru" in ics
    assert 'CN="A, B"' in ics  # comma forces quoting
    (parsed,) = parse_events(ics)
    assert parsed.organizer.email == "me@yandex.ru"
    assert parsed.attendees[0].email == "a@x.ru"
    assert parsed.attendees[0].name == "A, B"
    assert parsed.attendees[0].rsvp is True


@pytest.mark.parametrize(
    "attendee",
    [
        Attendee(email="safe@example.com\r\nX-INJECTED:YES"),
        Attendee(email="safe@example.com", name="Safe\r\nX-INJECTED:YES"),
        Attendee(email="safe@example.com", role="REQ-PARTICIPANT\r\nX-INJECTED:YES"),
        Attendee(email="safe@example.com", partstat="ACCEPTED\r\nX-INJECTED:YES"),
    ],
)
def test_build_rejects_line_break_in_calendar_address(attendee):
    event = Event(uid="injection", attendees=[attendee])
    with pytest.raises(ValueError, match="line break"):
        build_calendar(event)


@pytest.mark.parametrize(
    "email",
    [
        "not-an-email",
        "first@second@example.com",
        "mailto:safe@example.com",
        "safe@example.com?subject=unexpected",
        "safe@example.com;mailto:other@example.com",
    ],
)
def test_build_preserves_server_calendar_address(email):
    event = Event(uid="invalid-address", attendees=[Attendee(email=email)])
    assert email in build_calendar(event)


def test_build_normalizes_text_line_breaks_before_escaping():
    event = Event(uid="multiline", description="first\r\nsecond\rthird")
    ics = build_calendar(event)
    assert "DESCRIPTION:first\\nsecond\\nthird\r\n" in ics


def test_build_rejects_line_break_in_raw_property():
    event = Event(uid="event", raw_props=["X-CUSTOM:value\r\nX-INJECTED:YES"])
    with pytest.raises(ValueError, match="line break"):
        build_calendar(event)


def test_raw_props_are_preserved_on_roundtrip():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:r-1\r\n"
        "SUMMARY:Weekly\r\n"
        "DTSTART:20260101T090000Z\r\n"
        "RRULE:FREQ=WEEKLY;BYDAY=MO\r\n"
        "SEQUENCE:3\r\n"
        "BEGIN:VALARM\r\n"
        "ACTION:DISPLAY\r\n"
        "TRIGGER:-PT15M\r\n"
        "END:VALARM\r\n"
        "END:VEVENT\r\n"
    )
    (event,) = parse_events(text)
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in event.raw_props
    assert "BEGIN:VALARM" in event.raw_props and "END:VALARM" in event.raw_props
    # editing the summary must not drop the recurrence rule or the alarm
    event.summary = "Weekly (renamed)"
    ics = build_calendar(event)
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in ics
    assert "BEGIN:VALARM" in ics and "ACTION:DISPLAY" in ics
    assert "SUMMARY:Weekly (renamed)" in ics


def test_dtstamp_added_when_requested_and_not_duplicated():
    with_stamp = build_calendar(Event(uid="s-1"), dtstamp=datetime(2026, 7, 25, 12, tzinfo=UTC))
    assert "DTSTAMP:20260725T120000Z" in with_stamp
    # an event that already carries a DTSTAMP keeps its own, no duplicate
    ev = Event(uid="s-2", raw_props=["DTSTAMP:20200101T000000Z"])
    out = build_calendar(ev, dtstamp=datetime(2026, 7, 25, 12, tzinfo=UTC))
    assert out.count("DTSTAMP") == 1
    assert "DTSTAMP:20200101T000000Z" in out


VALARM_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:evt-alarm
SUMMARY:Standup
DESCRIPTION:Daily sync notes
DTSTART:20260725T090000Z
RRULE:FREQ=WEEKLY;BYDAY=MO
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:Reminder popup
TRIGGER:-PT10M
END:VALARM
END:VEVENT
END:VCALENDAR"""


def test_valarm_properties_do_not_leak_into_the_event():
    """A VALARM carries its own DESCRIPTION; it must not become the event's."""
    event = parse_events(VALARM_ICS)[0]
    assert event.summary == "Standup"
    assert event.description == "Daily sync notes"


def test_valarm_block_is_preserved_verbatim():
    event = parse_events(VALARM_ICS)[0]
    rebuilt = build_calendar(event)
    assert "BEGIN:VALARM" in rebuilt
    assert "DESCRIPTION:Reminder popup" in rebuilt
    assert "TRIGGER:-PT10M" in rebuilt
    assert "END:VALARM" in rebuilt
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in rebuilt
    # the event's own DESCRIPTION is still emitted exactly once
    assert rebuilt.count("DESCRIPTION:Daily sync notes") == 1


def test_nested_component_round_trips_through_reparse():
    event = parse_events(VALARM_ICS)[0]
    reparsed = parse_events(build_calendar(event))[0]
    assert reparsed.description == "Daily sync notes"
    assert any("VALARM" in line for line in reparsed.raw_props)


# --- verbatim DTSTART/DTEND and VTIMEZONE round-tripping -------------------

ZONED_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Yandex LLC//Yandex Calendar//EN
X-WR-CALNAME:Work
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
BEGIN:DAYLIGHT
DTSTART:19700329T020000
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
END:VTIMEZONE
BEGIN:VEVENT
UID:standup-1
SUMMARY:Standup
DTSTART;TZID=Europe/Berlin:20260105T100000
DTEND;TZID=Europe/Berlin:20260105T103000
RRULE:FREQ=WEEKLY;BYDAY=MO
EXDATE;TZID=Europe/Berlin:20260112T100000
END:VEVENT
END:VCALENDAR"""


def lines_of(ics: str) -> list[str]:
    return ics.split("\r\n")


def vevent_of(ics: str) -> list[str]:
    """Only the VEVENT's own lines — a VTIMEZONE carries DTSTARTs of its own."""
    lines = lines_of(ics)
    return lines[lines.index("BEGIN:VEVENT") + 1 : lines.index("END:VEVENT")]


def test_renaming_a_zoned_event_keeps_its_local_times():
    """The reported corruption: a title-only edit moved a Berlin standup by an hour."""
    event = parse_events(ZONED_ICS)[0]
    event.summary = "Standup (renamed)"
    out = vevent_of(build_calendar(event))
    assert "DTSTART;TZID=Europe/Berlin:20260105T100000" in out
    assert "DTEND;TZID=Europe/Berlin:20260105T103000" in out
    assert not any(line.startswith(("DTSTART:", "DTEND:")) for line in out)
    assert "SUMMARY:Standup (renamed)" in out
    # the EXDATE still points at a TZID the document defines
    assert "EXDATE;TZID=Europe/Berlin:20260112T100000" in out


def test_renaming_a_zoned_event_keeps_the_timezone_definition():
    event = parse_events(ZONED_ICS)[0]
    event.summary = "Standup (renamed)"
    out = lines_of(build_calendar(event))
    assert "BEGIN:VTIMEZONE" in out
    assert "TZID:Europe/Berlin" in out
    # the nested sub-components come through whole, with their transition rules
    for line in (
        "BEGIN:STANDARD",
        "TZOFFSETFROM:+0200",
        "TZOFFSETTO:+0100",
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0200",
        "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
    ):
        assert line in out


def test_vtimezone_is_emitted_before_the_vevent():
    """RFC 5545 wants the zone defined before the component that references it."""
    out = lines_of(build_calendar(parse_events(ZONED_ICS)[0]))
    assert out.index("BEGIN:VTIMEZONE") < out.index("BEGIN:VEVENT")
    assert out.index("END:VTIMEZONE") < out.index("BEGIN:VEVENT")
    assert out.index("BEGIN:VCALENDAR") < out.index("BEGIN:VTIMEZONE")


def test_assigning_a_new_start_is_written_in_the_events_own_zone():
    """The memory goes, the anchoring stays.

    12:00Z is 13:00 in Berlin in January. Writing the instant as ``…T120000Z``
    would be the same moment but a different kind of event: the series would
    stop following Berlin and drift an hour at the next changeover, which is
    the bug this file exists to fix, reached by rescheduling instead of by
    renaming.
    """
    event = parse_events(ZONED_ICS)[0]
    event.start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    assert event.raw_dt_lines.get("DTSTART") is None
    out = vevent_of(build_calendar(event))
    assert "DTSTART;TZID=Europe/Berlin:20260105T130000" in out
    # the end was not touched, so it keeps its original line
    assert "DTEND;TZID=Europe/Berlin:20260105T103000" in out


def test_assigning_a_new_end_touches_only_the_end():
    event = parse_events(ZONED_ICS)[0]
    event.end = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    out = lines_of(build_calendar(event))
    assert "DTEND;TZID=Europe/Berlin:20260105T110000" in out
    assert "DTSTART;TZID=Europe/Berlin:20260105T100000" in out


def test_a_changed_time_falls_back_to_utc_when_the_zone_cannot_be_defined():
    """Never emit a TZID this build cannot define in the same document."""
    text = (
        ZONED_ICS[: ZONED_ICS.index("BEGIN:VTIMEZONE")]
        + ZONED_ICS[ZONED_ICS.index("BEGIN:VEVENT") :]
    )
    event = parse_events(text)[0]
    assert event.timezones == []
    event.start = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
    out = vevent_of(build_calendar(event))
    assert "DTSTART:20260105T120000Z" in out
    assert not any(line.startswith("DTSTART;TZID") for line in out)


def test_assigning_the_same_instant_in_another_zone_keeps_the_original_line():
    """10:00 Berlin and 09:00Z are the same moment; the richer line stays."""
    event = parse_events(ZONED_ICS)[0]
    event.start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    assert "DTSTART;TZID=Europe/Berlin:20260105T100000" in lines_of(build_calendar(event))


def test_changing_all_day_forgets_both_remembered_lines():
    event = parse_events(ZONED_ICS)[0]
    event.all_day = True
    out = lines_of(build_calendar(event))
    assert "DTSTART;VALUE=DATE:20260105" in out
    # The DTEND is deliberately not asserted. Turning a timed event all-day the
    # way _format_dt does it yields DTEND equal to DTSTART, and for VALUE=DATE
    # the RFC makes DTEND exclusive, so that is a zero-length day. It predates
    # this change and is documented as a known problem rather than pinned here
    # as intended output.
    assert not any(
        line.startswith("DTEND;VALUE=DATE:2026010") and ":20260105" not in line for line in out
    )


def test_reasserting_all_day_unchanged_keeps_the_remembered_lines():
    """An update that passes all_day=false on an already-timed event changes nothing."""
    event = parse_events(ZONED_ICS)[0]
    event.all_day = False
    assert "DTSTART;TZID=Europe/Berlin:20260105T100000" in lines_of(build_calendar(event))


def test_remembered_lines_are_dropped_from_the_model_not_just_the_output():
    event = parse_events(ZONED_ICS)[0]
    assert set(event.raw_dt_lines) == {"DTSTART", "DTEND"}
    event.start = datetime(2026, 2, 2, 8, 0, tzinfo=UTC)
    assert set(event.raw_dt_lines) == {"DTEND"}


def test_an_event_built_from_scratch_remembers_nothing():
    event = Event(uid="new-1", start=datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    assert event.raw_dt_lines == {}
    assert event.timezones == []
    assert "DTSTART:20260105T090000Z" in build_calendar(event)


UTC_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:utc-1
SUMMARY:Sync
DTSTART:20260725T090000Z
DTEND:20260725T093000Z
END:VEVENT
END:VCALENDAR"""


def test_an_event_without_a_tzid_is_unchanged_by_the_round_trip():
    event = parse_events(UTC_ICS)[0]
    event.summary = "Sync (renamed)"
    out = build_calendar(event)
    assert "BEGIN:VTIMEZONE" not in out
    assert out == (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//hermes-yandex-calendar//EN\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:utc-1\r\n"
        "SUMMARY:Sync (renamed)\r\n"
        "DTSTART:20260725T090000Z\r\n"
        "DTEND:20260725T093000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def test_a_floating_event_stays_floating():
    text = (
        "BEGIN:VEVENT\r\n"
        "UID:float-1\r\n"
        "DTSTART:20260725T080000\r\n"
        "DTEND:20260725T090000\r\n"
        "END:VEVENT\r\n"
    )
    event = parse_events(text)[0]
    event.summary = "Floating"
    out = lines_of(build_calendar(event))
    assert "DTSTART:20260725T080000" in out
    assert "DTEND:20260725T090000" in out


def test_an_all_day_event_keeps_its_date_lines():
    event = parse_events(
        "BEGIN:VEVENT\r\n"
        "UID:day-2\r\n"
        "DTSTART;VALUE=DATE:20260101\r\n"
        "DTEND;VALUE=DATE:20260102\r\n"
        "END:VEVENT\r\n"
    )[0]
    event.summary = "Holiday (renamed)"
    out = lines_of(build_calendar(event))
    assert "DTSTART;VALUE=DATE:20260101" in out
    assert "DTEND;VALUE=DATE:20260102" in out


UNKNOWN_ZONE_ICS = """BEGIN:VCALENDAR
BEGIN:VTIMEZONE
TZID:Customer/Private-Zone
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0530
TZOFFSETTO:+0530
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:unknown-1
SUMMARY:Call
DTSTART;TZID=Customer/Private-Zone:20260105T100000
DTEND;TZID=Customer/Private-Zone:20260105T110000
END:VEVENT
END:VCALENDAR"""


def test_a_tzid_the_zone_lookup_does_not_know_still_round_trips():
    """zoneinfo cannot resolve the TZID, so the only correct answer is the original line."""
    event = parse_events(UNKNOWN_ZONE_ICS)[0]
    assert isinstance(event.start, datetime)
    assert event.start.tzinfo is None  # unresolvable zone parses as floating
    event.summary = "Call (renamed)"
    out = lines_of(build_calendar(event))
    assert "DTSTART;TZID=Customer/Private-Zone:20260105T100000" in out
    assert "DTEND;TZID=Customer/Private-Zone:20260105T110000" in out
    assert "TZID:Customer/Private-Zone" in out


def test_an_unparsable_time_is_handed_back_untouched():
    """A value the parser cannot read is the one it should least feel free to rewrite."""
    event = parse_events(
        "BEGIN:VEVENT\r\n"
        "UID:bad-1\r\n"
        "DTSTART;TZID=Europe/Berlin:20260105T100000\r\n"
        "DTEND;TZID=Europe/Berlin:20260105T990000\r\n"
        "END:VEVENT\r\n"
    )[0]
    assert event.end is None  # 99:00 is not a time
    assert event.all_day is False
    out = vevent_of(build_calendar(event))
    assert "DTEND;TZID=Europe/Berlin:20260105T990000" in out
    assert "DTSTART;TZID=Europe/Berlin:20260105T100000" in out


def test_an_unparsable_date_is_handed_back_untouched():
    event = parse_events(
        "BEGIN:VEVENT\r\nUID:bad-2\r\nDTSTART;VALUE=DATE:20261301\r\nEND:VEVENT\r\n"
    )[0]
    assert event.start is None  # there is no thirteenth month
    assert event.all_day is True
    assert "DTSTART;VALUE=DATE:20261301" in vevent_of(build_calendar(event))


TWO_ZONES_ICS = """BEGIN:VCALENDAR
BEGIN:VTIMEZONE
TZID:Europe/Berlin
BEGIN:STANDARD
DTSTART:19701025T030000
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
END:STANDARD
END:VTIMEZONE
BEGIN:VTIMEZONE
TZID:Europe/Moscow
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0300
TZOFFSETTO:+0300
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:two-zones
SUMMARY:Handover
DTSTART;TZID=Europe/Berlin:20260105T100000
EXDATE;TZID=Europe/Moscow:20260112T120000
END:VEVENT
END:VCALENDAR"""


def test_every_referenced_timezone_survives():
    out = lines_of(build_calendar(parse_events(TWO_ZONES_ICS)[0]))
    assert "TZID:Europe/Berlin" in out
    assert "TZID:Europe/Moscow" in out
    assert out.count("BEGIN:VTIMEZONE") == 2


def test_a_timezone_nothing_references_is_kept_too():
    """Deciding what is referenced needs a full RFC 5545 reader; keeping it is safe."""
    text = TWO_ZONES_ICS.replace("EXDATE;TZID=Europe/Moscow:20260112T120000\n", "")
    out = lines_of(build_calendar(parse_events(text)[0]))
    assert "TZID:Europe/Moscow" in out


def test_timezones_are_not_duplicated_by_a_second_round_trip():
    once = build_calendar(parse_events(ZONED_ICS)[0])
    twice = build_calendar(parse_events(once)[0])
    assert twice.count("BEGIN:VTIMEZONE") == 1
    assert twice == once


def test_a_repeated_timezone_definition_is_emitted_once():
    doubled = TWO_ZONES_ICS.replace("TZID:Europe/Moscow", "TZID:Europe/Berlin")
    out = build_calendar(parse_events(doubled)[0])
    assert out.count("BEGIN:VTIMEZONE") == 1


def test_one_shared_timezone_reaches_every_event_in_the_document():
    text = ZONED_ICS.replace(
        "END:VCALENDAR",
        "BEGIN:VEVENT\n"
        "UID:standup-2\n"
        "SUMMARY:Retro\n"
        "DTSTART;TZID=Europe/Berlin:20260106T100000\n"
        "END:VEVENT\n"
        "END:VCALENDAR",
    )
    first, second = parse_events(text)
    for event in (first, second):
        assert "TZID:Europe/Berlin" in lines_of(build_calendar(event))
    # each event owns its copy: editing one document's event cannot corrupt another
    first.timezones.clear()
    assert "TZID:Europe/Berlin" in lines_of(build_calendar(second))


def test_calendar_level_properties_are_not_carried_over():
    """Only the zone definitions ride along; the producer's own header stays behind."""
    out = build_calendar(parse_events(ZONED_ICS)[0])
    assert "X-WR-CALNAME" not in out
    assert "Yandex LLC" not in out
    assert "PRODID:-//hermes-yandex-calendar//EN" in out
    assert out.count("PRODID") == 1


def test_an_unterminated_timezone_is_dropped():
    """A complete document, only the END:VTIMEZONE missing.

    The terminator matters: with END:VCALENDAR present, a collector that closes
    on BEGIN/END depth alone is balanced by the VEVENT's own END and hands back
    the rest of the file as a "timezone", which build_calendar then splices in
    ahead of the real VEVENT — two events under one UID in the PUT body.
    """
    truncated = (
        ZONED_ICS[: ZONED_ICS.index("END:VTIMEZONE")]
        + "BEGIN:VEVENT\nUID:x\nSUMMARY:Stale\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    event = parse_events(truncated)[0]
    event.summary = "Edited"
    out = build_calendar(event)
    assert "BEGIN:VTIMEZONE" not in out
    assert out.count("BEGIN:VEVENT") == 1
    assert out.count("END:VCALENDAR") == 1
    assert "SUMMARY:Stale" not in out


def test_a_second_timezone_after_an_unterminated_one_is_still_collected():
    """Abandoning a block must not blind the collector to the next one."""
    opening = ZONED_ICS[: ZONED_ICS.index("END:VTIMEZONE")]
    text = (
        opening
        + "BEGIN:VTIMEZONE\nTZID:Europe/Lisbon\nBEGIN:STANDARD\nTZOFFSETFROM:+0100\n"
        + "TZOFFSETTO:+0000\nEND:STANDARD\nEND:VTIMEZONE\n"
        + "BEGIN:VEVENT\nUID:x\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    out = build_calendar(parse_events(text)[0])
    assert out.count("BEGIN:VTIMEZONE") == 1
    assert "TZID:Europe/Lisbon" in out
    assert "TZID:Europe/Berlin" not in out


def test_a_blank_line_inside_a_timezone_is_not_written_back():
    """RFC 5545 has no empty content line; a strict server can refuse the PUT."""
    text = ZONED_ICS.replace("TZID:Europe/Berlin\n", "TZID:Europe/Berlin\n\n")
    out = build_calendar(parse_events(text)[0])
    assert "BEGIN:VTIMEZONE" in out
    assert all(line for line in out.split("\r\n")[:-1])


def test_a_timezone_without_a_tzid_is_still_carried():
    text = ZONED_ICS.replace("TZID:Europe/Berlin\n", "")
    out = build_calendar(parse_events(text)[0])
    assert out.count("BEGIN:VTIMEZONE") == 1
    assert "TZOFFSETTO:+0200" in out


def test_a_remembered_line_cannot_be_handed_in_through_the_constructor():
    """Only the parser establishes a memory, so it always matches the times beside it."""
    with pytest.raises(TypeError):
        Event(uid="x", raw_dt_lines={"DTSTART": "DTSTART;TZID=Europe/Berlin:20260105T100000"})


def test_replacing_a_field_starts_the_copy_without_a_memory():
    """dataclasses.replace rebuilds the event, so it must not inherit stale lines."""
    event = parse_events(ZONED_ICS)[0]
    edited = replace(event, start=datetime(2026, 1, 5, 12, 0, tzinfo=UTC))
    assert edited.raw_dt_lines == {}
    whole = build_calendar(edited)
    out = vevent_of(whole)
    assert "DTSTART;TZID=Europe/Berlin:20260105T130000" in out
    assert "DTEND;TZID=Europe/Berlin:20260105T103000" in out
    # The copy keeps raw_props, TZID references and all, so it must keep the
    # definitions too — otherwise replace() produces the invalid document this
    # whole change exists to prevent.
    assert "TZID:Europe/Berlin" in whole
    assert "BEGIN:VTIMEZONE" in whole
