"""Minimal iCalendar (RFC 5545) VEVENT parsing and serialization.

Deliberately dependency-free and scoped to what this plugin needs: a single
VEVENT per resource with SUMMARY / DTSTART / DTEND / LOCATION / DESCRIPTION /
UID, plus ORGANIZER / ATTENDEE / TRANSP for meeting management. It is NOT a
general iCalendar implementation — it handles line (un)folding, TEXT escaping,
and the three DTSTART/DTEND forms Yandex emits (UTC ``...Z``, ``TZID=...``
local, and ``VALUE=DATE`` all-day).

Any property it does not model (RRULE, VALARM blocks, SEQUENCE, …) is preserved
verbatim in ``Event.raw_props`` and re-emitted on build, so editing an event
never silently drops recurrence rules or alarms.

The same holds for the times themselves. A DTSTART/DTEND the caller does not
change is written back as the exact line it was read as, kept in
``Event.raw_dt_lines``, so a local time keeps its ``TZID`` instead of being
flattened into UTC; and the document's ``VTIMEZONE`` components ride along in
``Event.timezones``, so those TZID references still resolve. Only a time the
caller actually assigns is re-formatted, as a UTC instant.

No Hermes imports here so it stays unit-testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

try:  # zoneinfo is stdlib on >=3.9; guard so a missing tzdata never crashes parsing
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - zoneinfo effectively always present on 3.11+
    ZoneInfo = None  # type: ignore[assignment]

__all__ = ["Attendee", "Event", "build_calendar", "parse_events"]

TRANSP_BUSY = "OPAQUE"
TRANSP_FREE = "TRANSPARENT"


@dataclass
class Attendee:
    """A meeting participant (ATTENDEE / ORGANIZER)."""

    email: str
    name: str = ""
    role: str = ""  # e.g. REQ-PARTICIPANT, OPT-PARTICIPANT, CHAIR
    partstat: str = ""  # e.g. NEEDS-ACTION, ACCEPTED, DECLINED, TENTATIVE
    rsvp: bool | None = None


# Which remembered DTSTART/DTEND lines a given field describes. Assigning the
# field a different value makes those lines stale, so they are forgotten.
_DT_LINES_BY_FIELD = {
    "start": ("DTSTART",),
    "end": ("DTEND",),
    # all_day decides between ";VALUE=DATE" and a date-time for both ends.
    "all_day": ("DTSTART", "DTEND"),
}

_UNSET = object()


@dataclass
class Event:
    """A calendar event.

    ``href`` (the CalDAV resource path) and ``etag`` (the version the server gave
    that resource) are both filled in by the client when an event is read, and are
    not part of the iCalendar data.

    ``start``/``end`` are the times as this model understands them: an instant for
    a UTC or resolvable-``TZID`` value, a naive datetime for a floating one or a
    zone :mod:`zoneinfo` cannot resolve, a :class:`date` for an all-day event.
    That is lossy on purpose — it is what the tool layer reports and compares —
    so the original lines are kept alongside in ``raw_dt_lines`` and rewritten
    verbatim for as long as they still describe ``start``/``end``.
    """

    uid: str
    summary: str = ""
    start: datetime | date | None = None
    end: datetime | date | None = None
    location: str = ""
    description: str = ""
    all_day: bool = False
    organizer: Attendee | None = None
    attendees: list[Attendee] = field(default_factory=list)
    transp: str = ""  # OPAQUE (busy) / TRANSPARENT (free); "" == unset
    href: str = ""
    etag: str = ""  # version tag from the server; enables conditional writes
    # Verbatim, already-unfolded content lines for properties we don't model.
    raw_props: list[str] = field(default_factory=list)
    # {"DTSTART"/"DTEND": verbatim line}, as parsed. Empty for a new event.
    # Not a constructor argument: the only way in is the parser, so a line can
    # never arrive already contradicting the start or end it is stored beside,
    # and ``dataclasses.replace`` starts an edited copy with no memory at all.
    raw_dt_lines: dict[str, str] = field(
        default_factory=dict, init=False, compare=False, repr=False
    )
    # The VTIMEZONE components of the document this event came from, each as its
    # verbatim lines. They belong to the calendar rather than to one event, but
    # the model that leaves this class is a single Event, so every event parsed
    # from a document carries that document's definitions and can be rebuilt on
    # its own without orphaning its TZID references.
    # Unlike ``raw_dt_lines`` this one IS a constructor argument: a copy made
    # with ``dataclasses.replace`` keeps the TZID references sitting in
    # ``raw_props``, so dropping the definitions would hand back exactly the
    # invalid document this field exists to prevent. A zone definition cannot
    # contradict the times stored beside it, so there is nothing to guard.
    timezones: list[list[str]] = field(default_factory=list, compare=False, repr=False)
    # {"DTSTART"/"DTEND": TZID}, as parsed. Unlike the remembered lines this is
    # NOT forgotten when the time changes: the zone is what the event is
    # anchored to, not what it currently reads, and a caller who reschedules a
    # 10:00 Berlin series to 11:00 means 11:00 in Berlin. Writing the new time
    # as a UTC instant instead would re-anchor the series and move it again at
    # the next daylight-saving change — the very bug this file is fixing.
    dt_zones: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def __setattr__(self, name: str, value: object) -> None:
        """Forget a remembered DTSTART/DTEND line as soon as it stops being true.

        Every write lands here, so no caller has to remember a bookkeeping step:
        giving ``start``, ``end`` or ``all_day`` a new value drops the verbatim
        line for the properties it describes, and that property is formatted from
        the model instead. Re-assigning the value it already has keeps the line —
        it still denotes exactly that value, and an update that repeats an
        unchanged ``all_day`` must not cost the event its time zone.
        """
        remembered = getattr(self, "raw_dt_lines", None)
        stale = _DT_LINES_BY_FIELD.get(name)
        if remembered and stale and getattr(self, name, _UNSET) != value:
            for prop in stale:
                remembered.pop(prop, None)
        super().__setattr__(name, value)


# --- line handling ---------------------------------------------------------


def _unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding: a line beginning with space/tab continues the prior."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: list[str] = []
    for line in raw:
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuation lines prefixed with a space."""
    if "\r" in line or "\n" in line:
        raise ValueError("iCalendar content lines must not contain a line break.")
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    out: list[str] = []
    chunk = bytearray()
    for ch in line:
        b = ch.encode("utf-8")
        # keep the multibyte char whole; 74 leaves room for the leading space on next line
        limit = 75 if not out else 74
        if len(chunk) + len(b) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = bytearray()
        chunk += b
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def _unescape(value: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            result.append({"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _escape(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _split_prop(line: str) -> tuple[str, dict[str, str], str]:
    """Return (NAME, params, value) for a content line like ``DTSTART;TZID=X:val``."""
    pieces: list[str] = []
    chunk: list[str] = []
    quoted = False
    value = ""
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        if char == ":" and not quoted:
            pieces.append("".join(chunk))
            value = line[index + 1 :]
            break
        if char == ";" and not quoted:
            pieces.append("".join(chunk))
            chunk = []
        else:
            chunk.append(char)
    else:
        pieces.append("".join(chunk))
    name = pieces[0].upper()
    params: dict[str, str] = {}
    for piece in pieces[1:]:
        key, _, val = piece.partition("=")
        params[key.upper()] = val.strip('"')
    return name, params, value


def _param_value(value: str) -> str:
    """Quote a parameter value if it contains characters that require it (RFC 5545)."""
    if any(c in value for c in ',;:"'):
        return '"' + value.replace('"', "") + '"'
    return value


def _parse_cal_address(value: str, params: dict[str, str]) -> Attendee:
    """Parse an ORGANIZER/ATTENDEE value (``mailto:x@y``) plus its parameters."""
    email = value.strip()
    if email.lower().startswith("mailto:"):
        email = email[len("mailto:") :]
    rsvp_raw = params.get("RSVP", "").upper()
    rsvp = True if rsvp_raw == "TRUE" else False if rsvp_raw == "FALSE" else None
    return Attendee(
        email=email,
        name=params.get("CN", ""),
        role=params.get("ROLE", ""),
        partstat=params.get("PARTSTAT", ""),
        rsvp=rsvp,
    )


def _validate_cal_address(attendee: Attendee) -> None:
    values = (attendee.email, attendee.name, attendee.role, attendee.partstat)
    if any("\r" in value or "\n" in value for value in values):
        raise ValueError("Calendar addresses must not contain a line break.")


def _format_cal_address(prop: str, attendee: Attendee) -> str:
    """Serialize an ATTENDEE/ORGANIZER content line."""
    _validate_cal_address(attendee)
    parts = [prop]
    if attendee.name:
        parts.append(f"CN={_param_value(attendee.name)}")
    if attendee.role:
        parts.append(f"ROLE={_param_value(attendee.role)}")
    if attendee.partstat:
        parts.append(f"PARTSTAT={_param_value(attendee.partstat)}")
    if attendee.rsvp is not None:
        parts.append(f"RSVP={'TRUE' if attendee.rsvp else 'FALSE'}")
    address = attendee.email
    if ":" not in address:
        address = f"mailto:{address}"
    return ";".join(parts) + f":{address}"


# --- datetime handling -----------------------------------------------------


def _localize(dt: datetime, tzid: str | None) -> datetime:
    """Attach the TZID's zone, or leave the value floating if it is unusable."""
    if not tzid or ZoneInfo is None:
        return dt
    try:
        return dt.replace(tzinfo=ZoneInfo(tzid))
    except Exception:  # unknown zone -> keep naive rather than fail the parse
        return dt


def _parse_dt(value: str, params: dict[str, str]) -> tuple[datetime | date | None, bool]:
    """Parse a DTSTART/DTEND value. Returns (value, is_all_day)."""
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(value) == 8 and "T" not in value):
        try:
            return date(int(value[0:4]), int(value[4:6]), int(value[6:8])), True
        except ValueError:
            return None, True
    is_utc = value.endswith("Z")
    try:
        dt = datetime.strptime(value[:-1] if is_utc else value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    if is_utc:
        return dt.replace(tzinfo=UTC), False
    return _localize(dt, params.get("TZID")), False


def _format_dt(value: datetime | date, all_day: bool) -> tuple[str, str]:
    """Return (param_suffix, formatted_value) for a DTSTART/DTEND line."""
    if all_day or (isinstance(value, date) and not isinstance(value, datetime)):
        return ";VALUE=DATE", f"{value.year:04d}{value.month:02d}{value.day:02d}"
    dt: datetime = value  # type: ignore[assignment]
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC)
        return "", dt.strftime("%Y%m%dT%H%M%SZ")
    # naive == floating local time; emit without Z
    return "", dt.strftime("%Y%m%dT%H%M%S")


# --- public API ------------------------------------------------------------

_TEXT_PROPS = {"SUMMARY": "summary", "LOCATION": "location", "DESCRIPTION": "description"}


def _apply_property(event: Event, line: str) -> None:
    """Fold one VEVENT content line into ``event``.

    Anything this model does not cover (RRULE, SEQUENCE, X- extensions, …) is kept
    verbatim in ``raw_props`` so it survives a parse/build round-trip. DTSTART and
    DTEND are modelled *and* kept verbatim in ``raw_dt_lines``: the model drives
    the tool layer, the line is what goes back on the wire while the caller leaves
    the time alone. Each is remembered after the parsed values are assigned,
    because assigning them is what clears the memory.
    """
    name, params, value = _split_prop(line)
    if name == "UID":
        event.uid = _unescape(value)
    elif name in _TEXT_PROPS:
        setattr(event, _TEXT_PROPS[name], _unescape(value))
    elif name == "DTSTART":
        event.start, event.all_day = _parse_dt(value, params)
        event.raw_dt_lines["DTSTART"] = line
        _remember_zone(event, "DTSTART", params)
    elif name == "DTEND":
        end, all_day = _parse_dt(value, params)
        event.end = end
        event.all_day = event.all_day or all_day
        event.raw_dt_lines["DTEND"] = line
        _remember_zone(event, "DTEND", params)
    elif name == "TRANSP":
        event.transp = value.strip().upper()
    elif name == "ORGANIZER":
        event.organizer = _parse_cal_address(value, params)
    elif name == "ATTENDEE":
        event.attendees.append(_parse_cal_address(value, params))
    else:
        event.raw_props.append(line)


def _remember_zone(event: Event, prop: str, params: dict[str, str]) -> None:
    """Note the TZID this end was written in, so a new time can use it too."""
    tzid = params.get("TZID", "").strip()
    if tzid:
        event.dt_zones[prop] = tzid


def parse_events(text: str) -> list[Event]:
    """Parse every VEVENT found in an iCalendar document.

    The document's VTIMEZONE components are collected too and attached to every
    event, so an event written back on its own still defines the zones its TZID
    parameters name. Nothing else outside the VEVENTs is kept.
    """
    lines = _unfold(text)
    timezones = _parse_timezones(lines)
    events = _parse_vevents(lines)
    for event in events:
        # a copy each: editing one event must not reach the others
        event.timezones = [list(component) for component in timezones]
    return events


def _timezone_key(component: list[str]) -> str:
    """The TZID this VTIMEZONE defines, or its whole text when it names none."""
    for line in component[1:]:
        name, _params, value = _split_prop(line)
        if name == "TZID":
            return value.strip()
        if name == "BEGIN":  # into STANDARD/DAYLIGHT: no TZID at this level
            break
    return "\n".join(component)


def _parse_timezones(lines: list[str]) -> list[list[str]]:
    """Every VTIMEZONE in the document, verbatim, at most one per TZID.

    Sub-components (STANDARD, DAYLIGHT) are part of the block and come along;
    their lines need no special handling, because only ``END:VTIMEZONE`` closes
    a block. A block the document never closes is dropped rather than
    half-emitted: a counter of BEGIN/END depth would be balanced by the VEVENT's
    own END and the calendar's, and the "component" would come back holding the
    rest of the file, to be spliced in front of the real VEVENT.
    """
    components: list[list[str]] = []
    seen: set[str] = set()
    current: list[str] | None = None
    for line in lines:
        if not line:
            # RFC 5545 has no empty content line; re-emitting one can cost a PUT.
            continue
        upper = line.upper()
        if upper.startswith("BEGIN:VTIMEZONE"):
            current = [line]  # abandons an unclosed block, if any
        elif current is None:
            continue
        elif upper.startswith(("BEGIN:VEVENT", "END:VCALENDAR")):
            current = None  # this block was never closed
        else:
            current.append(line)
            if upper.startswith("END:VTIMEZONE"):
                _remember_timezone(components, seen, current)
                current = None
    return components


def _remember_timezone(components: list[list[str]], seen: set[str], block: list[str]) -> None:
    """Keep the first block for each TZID; two with one TZID is invalid anyway."""
    key = _timezone_key(block)
    if key not in seen:
        seen.add(key)
        components.append(block)


def _parse_vevents(lines: list[str]) -> list[Event]:
    """Every VEVENT in the document. Anything outside one is not ours to keep."""
    events: list[Event] = []
    current: Event | None = None
    nested: list[str] = []  # open sub-components inside the VEVENT (VALARM, …)
    for line in lines:
        upper = line.upper()
        if current is None:
            if upper.startswith("BEGIN:VEVENT"):
                current = Event(uid="")
        elif nested:
            # A sub-component has its own SUMMARY/DESCRIPTION/TRIGGER, which must
            # not be read as the event's. Keep the block verbatim instead.
            current.raw_props.append(line)
            if upper.startswith("END:") and upper[4:].strip() == nested[-1]:
                nested.pop()
        elif upper.startswith("BEGIN:"):
            nested.append(upper[len("BEGIN:") :].strip())
            current.raw_props.append(line)
        elif upper.startswith("END:VEVENT"):
            events.append(current)
            current = None
        elif line:
            _apply_property(current, line)
    return events


def build_calendar(
    event: Event,
    *,
    prodid: str = "-//hermes-yandex-calendar//EN",
    dtstamp: datetime | None = None,
) -> str:
    """Serialize a single Event into a VCALENDAR document (CRLF-terminated).

    ``dtstamp`` (if given and the event carries no DTSTAMP of its own) is emitted
    as the required RFC 5545 DTSTAMP; the client passes ``now`` on create/update.

    The event's ``timezones`` are emitted ahead of the VEVENT, where RFC 5545
    wants them: a TZID must be defined before the component referencing it. The
    calendar's other properties are not ours to reproduce — the PRODID written
    here is this plugin's own.
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        *(line for component in event.timezones for line in component),
        "BEGIN:VEVENT",
        f"UID:{_escape(event.uid)}",
        *_event_lines(event, dtstamp),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _event_lines(event: Event, dtstamp: datetime | None) -> list[str]:
    """The VEVENT body: modelled properties first, then everything kept verbatim."""
    lines: list[str] = []
    has_dtstamp = any(p.upper().startswith("DTSTAMP") for p in event.raw_props)
    if dtstamp is not None and not has_dtstamp:
        lines.append(f"DTSTAMP:{dtstamp.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    for prop, value in (
        ("SUMMARY", event.summary),
        ("TRANSP", event.transp),
        ("LOCATION", event.location),
        ("DESCRIPTION", event.description),
    ):
        if value:
            # TRANSP carries an enum token, so escaping is a no-op for it.
            lines.append(f"{prop}:{_escape(value)}")
    lines.extend(_datetime_lines(event))
    if event.organizer is not None:
        lines.append(_format_cal_address("ORGANIZER", event.organizer))
    lines.extend(_format_cal_address("ATTENDEE", a) for a in event.attendees)
    lines.extend(event.raw_props)
    return lines


def _datetime_lines(event: Event) -> list[str]:
    """DTSTART/DTEND: the line as read while it still holds, otherwise formatted.

    A remembered line wins because it says more than the model can: the zone the
    user picked, or a value we failed to parse and must not silently drop.
    :meth:`Event.__setattr__` removes it the moment the value changes, so this
    can never emit a time the event no longer has.
    """
    lines: list[str] = []
    for prop, moment in (("DTSTART", event.start), ("DTEND", event.end)):
        remembered = event.raw_dt_lines.get(prop)
        if remembered is not None:
            lines.append(remembered)
        elif moment is not None:
            lines.append(
                _zoned_line(event, prop, moment) or _plain_line(prop, moment, event.all_day)
            )
    return lines


def _plain_line(prop: str, moment: datetime | date, all_day: bool) -> str:
    suffix, val = _format_dt(moment, all_day)
    return f"{prop}{suffix}:{val}"


def _zoned_line(event: Event, prop: str, moment: datetime | date) -> str | None:
    """A changed time, written in the zone the event was anchored to.

    Returns ``None`` whenever that cannot be done honestly: an all-day value has
    no zone, a floating time has none to convert from, the zone name may be one
    :mod:`zoneinfo` does not know, and above all the document must still define
    the TZID — emitting a reference this build cannot define would trade one
    invalid document for another. In each of those the caller falls back to the
    plain UTC form, which is what this module did for every value before.
    """
    tzid = event.dt_zones.get(prop)
    if not tzid or event.all_day or not isinstance(moment, datetime) or moment.tzinfo is None:
        return None
    if ZoneInfo is None or not any(_timezone_key(block) == tzid for block in event.timezones):
        return None
    try:
        local = moment.astimezone(ZoneInfo(tzid))
    except Exception:  # unknown zone -> the plain form is the honest fallback
        return None
    return f"{prop};TZID={tzid}:{local.strftime('%Y%m%dT%H%M%S')}"
