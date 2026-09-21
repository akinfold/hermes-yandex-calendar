# hermes-yandex-calendar

[![PyPI version](https://img.shields.io/pypi/v/hermes-yandex-calendar.svg)](https://pypi.org/project/hermes-yandex-calendar/)
[![CI](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml)
[![E2E (live)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/e2e.yml/badge.svg)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/e2e.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/akinfold/hermes-yandex-calendar/badges/coverage.json&v=1)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml)
[![CodeFactor](https://www.codefactor.io/repository/github/akinfold/hermes-yandex-calendar/badge)](https://www.codefactor.io/repository/github/akinfold/hermes-yandex-calendar)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Let your [Hermes Agent](https://hermes-agent.nousresearch.com) run your Yandex
Calendar.** *"What's on my calendar next week?"* — *"Move the standup to Thursday
and invite Ann."* — *"Decline the 4pm."* The agent reads and writes real events on
your real calendar, over CalDAV, with no third-party service in the middle.

- 📅 **Seven tools, one toolset** — list, create, update, RSVP, move between
  calendars, delete, and enumerate the calendars themselves.
- 🔒 **You choose what it may touch** — restrict it to specific calendars, and to
  specific actions (`read`, `read,write`, …). A disallowed action is not in the
  toolset at all.
- 🛟 **Careful with your data** — recurrence rules, alarms, and properties this
  plugin does not model survive every edit (an edit does rewrite a timed start and
  end into UTC and drops the resource's `VTIMEZONE`, so a recurring series in a
  daylight-saving zone shifts by an hour after the changeover); an edit that would overwrite someone
  else's concurrent change is refused; moves copy the resource byte for byte, and
  the original goes only once the copy has been read back from the target *and*
  only if nobody changed it in between.
- 🔑 **App password, not your account password** — scoped to CalDAV, revocable in
  one click.

Tested against Hermes **0.19.x**, Python **3.11–3.13**.

## Quick start

```bash
# 1. Install into Hermes (alternatively: pip install hermes-yandex-calendar)
hermes plugins install akinfold/hermes-yandex-calendar/hermes_yandex_calendar --enable

# 2. Add your credentials — the app password comes from
#    https://id.yandex.ru/security/app-passwords (scope: "Calendar (CalDAV)")
(umask 077 && printf 'YANDEX_CALENDAR_LOGIN=%s\nYANDEX_CALENDAR_APP_PASSWORD=%s\n' \
  'you@yandex.ru' 'your-app-password' >> ~/.hermes/.env)
chmod 600 ~/.hermes/.env
```

Then enable it in `~/.hermes/config.yaml` (third-party plugins are off by default):

```yaml
plugins:
  enabled: [yandex_calendar]
```

That's it. Ask the agent *"what do I have tomorrow?"* and it will tell you.

> The backward-compatible default exposes every action, including delete. For a
> new or untrusted agent setup, start with `YANDEX_CALENDAR_ACTIONS=read` — see
> [Security boundaries](#security-boundaries).

## The tools

Up to seven standalone tools, in the `yandex_calendar` toolset:

| Tool | Purpose |
|---|---|
| `yandex_calendar_list_calendars` | List the calendars the plugin can use (name + `href`). |
| `yandex_calendar_list_events` | List events in a time range (summary, start/end, location, description, attendees, busy status, and an `href`). |
| `yandex_calendar_create_event` | Create an event (summary, start, optional end/location/description/all-day, attendees, busy status, target calendar). |
| `yandex_calendar_update_event` | Edit an event by `href`: change fields, add/remove attendees, toggle busy/free; an empty string clears a text field. Recurrence rules and alarms are preserved, and a concurrent change by someone else is refused rather than overwritten. |
| `yandex_calendar_respond_event` | Respond to a meeting invitation — accept, decline, or tentatively accept. |
| `yandex_calendar_move_event` | Move an event to another calendar, contents intact. |
| `yandex_calendar_delete_event` | Delete an event by `href`. Idempotent: an `href` that no longer exists, or never did, is also reported as `deleted: true`. |

**Recurring events are not expanded.** `yandex_calendar_list_events` runs a plain
time-range query and reports each event as the server stores it. A recurring series
is listed once, with the `start`/`end` of its *first* occurrence — which can lie well
before the requested range — and its recurrence rule is not part of the output. A
series whose individual occurrences were edited comes back as several entries sharing
one `href`, and `update_event` and `respond_event` refuse such a resource rather than
drop those occurrences; it can still be moved or deleted as a whole.

Yandex Calendar has no public REST API, so this plugin speaks **CalDAV**
(`https://caldav.yandex.ru`) directly — the same protocol Yandex's own docs point
third-party clients at. Nothing is proxied through anyone else's servers.

### Multiple calendars

`yandex_calendar_list_events` and `yandex_calendar_create_event` take an optional
`calendar` argument; `yandex_calendar_move_event` takes a required one, naming the
destination. A calendar is named by its display **name** (as returned by
`yandex_calendar_list_calendars`), by the last segment of its path (e.g.
`events-12345`), or by its full `href` — names and path segments are matched
case-insensitively. Omit the optional argument to use the default calendar.
`update`, `respond`, `move`, and `delete` identify the event by its `event_href`,
which already encodes the calendar it lives in.

Restrict which calendars the plugin may touch with `YANDEX_CALENDAR_CALENDARS`. The
default calendar is the first **the server lists** among the allowed ones; the order
of that setting does not decide it.

Some Yandex calendars — holidays and birthdays, for instance — are read-only, yet they
are listed like any other and the server answers a write into them with success while
storing nothing. `yandex_calendar_move_event` catches this, because it reads the copy
back and leaves the original where it was, but `yandex_calendar_create_event` does not:
it reports `created: true` and an `href` at which nothing exists. Keep such calendars
out of reach with `YANDEX_CALENDAR_CALENDARS`.

## Configuration

| Env var | Required | Default | Meaning |
|---|---|---|---|
| `YANDEX_CALENDAR_LOGIN` | yes | — | Yandex login / email. |
| `YANDEX_CALENDAR_APP_PASSWORD` | yes | — | App password for CalDAV — an account password will not work. |
| `YANDEX_CALENDAR_BASE_URL` | no | `https://caldav.yandex.ru` | HTTPS override for self-hosted / testing. |
| `YANDEX_CALENDAR_CALENDARS` | no | *(all)* | Comma-separated allow-list of calendars, e.g. `Work,Personal`. Each entry is a calendar name or the last segment of its `href` (e.g. `events-12345`), matched case-insensitively; a full `href` is not accepted here. An entry that matches nothing is ignored, and if none match, `yandex_calendar_list_calendars` returns an empty list while every other tool fails with `No calendars available matching …`. |
| `YANDEX_CALENDAR_ACTIONS` | no | *(all)* | Comma-separated allow-list of actions the agent may perform — see below. |

Credentials are read from the environment first, then from `~/.hermes/.env`, so they
work in gateway and subprocess runs. Secret values are never logged.

Dates and times are ISO 8601 (`2026-07-25` or `2026-07-25T14:00:00+03:00`); a datetime
without an offset is treated as UTC.

### Restricting what the agent can do

`YANDEX_CALENDAR_ACTIONS` decides which of the seven tools are registered at all.
A disallowed action is not merely refused at call time: the tool never appears in
the agent's toolset, so it cannot be invoked, and the model is not tempted to try.

Accepted values, comma-separated and case-insensitive — individual actions
(`list_calendars`, `list_events`, `create_event`, `update_event`, `respond_event`,
`move_event`, `delete_event`), full tool names (`yandex_calendar_delete_event`), or
the shorthands:

| Shorthand | Expands to |
|---|---|
| `read` | `list_calendars`, `list_events` |
| `write` | `create_event`, `update_event`, `respond_event`, `move_event` |
| `delete` | `delete_event` |
| `all` | everything (the default) |

```dotenv
# Look, but don't touch:
YANDEX_CALENDAR_ACTIONS=read

# Full scheduling, but the agent can never delete anything:
YANDEX_CALENDAR_ACTIONS=read,write

# Just enough to answer invitations:
YANDEX_CALENDAR_ACTIONS=list_events,respond_event
```

Leave it unset for all seven tools; an empty or whitespace-only value counts as
unset, so `YANDEX_CALENDAR_ACTIONS=` does **not** mean "no tools". A name that
matches nothing is ignored, so a typo can only ever withhold a tool, never grant one
— and a non-blank value that names nothing recognisable therefore registers nothing
at all. The list is applied when the plugin loads: restart Hermes after changing it.

### Security boundaries

- CalDAV credentials are sent only to the HTTPS origin configured by
  `YANDEX_CALENDAR_BASE_URL`. Event hrefs pointing to HTTP or another origin are
  rejected before a request is made. Cross-origin redirects do not receive the
  authorization header.
- `YANDEX_CALENDAR_CALENDARS` applies to both target calendars and direct event
  operations (`update`, `respond`, `move`, and `delete`). Event paths are
  canonicalized before the allow-list check and the same canonical path is sent;
  moves validate both the source and destination calendar.
- Calendar titles, descriptions, locations, and attendee names are untrusted input.
  A capable agent can still act on misleading event content, so use `read` unless
  the agent and every calendar writer are trusted. Attendees supplied to a tool
  must be mailbox addresses, while server-provided calendar addresses are preserved
  for compatible invitation updates. Line breaks are rejected during serialization
  to prevent CRLF property injection.
- Edits are conditional. The plugin writes an event back only if it still matches
  the version it read (`If-Match` with the resource's `ETag`), so an agent cannot
  silently overwrite a change you made meanwhile in the Yandex web UI, on a phone,
  or from another client. When that happens the tool says so and the agent can
  re-read the event and reapply its change. A move is conditional at the same
  point: the original is removed only if it still matches the copy that was just
  written, and if it changed in between the event is left in both calendars with a
  message saying so, rather than losing that change.
- The action allow-list limits the tools exposed to Hermes; it does not reduce the
  privileges of the Yandex app password itself. Use a dedicated app password and,
  for stronger isolation, a dedicated Yandex account.

The default remains `all` for compatibility with existing installations. This is a
trusted-agent mode, not the recommended starting point for a new deployment.

## Getting the app password

CalDAV does not accept your normal account password.

1. Open <https://id.yandex.ru/security/app-passwords>.
2. Add a password with the **Calendar (CalDAV)** scope.
3. Copy it into `YANDEX_CALENDAR_APP_PASSWORD` — it is shown only once, and you can
   revoke it at any time without touching your account password.

If a tool answers *"Authentication failed"*, this is almost always the cause.

## Installing the plugin into Hermes

### Option A — from Git (recommended)

```bash
hermes plugins install akinfold/hermes-yandex-calendar/hermes_yandex_calendar --enable
```

Note the `/hermes_yandex_calendar` at the end. The plugin lives in that directory,
not at the repository root, and Hermes reads the manifest from whatever you point
it at. Name the directory and the install is a plugin: Hermes prompts for
`YANDEX_CALENDAR_LOGIN` and `YANDEX_CALENDAR_APP_PASSWORD`, installs under the
manifest name `yandex_calendar`, and `--enable` enables that name. It also scans
only that directory, so the tests and workflows in this repository stay out of the
security report.

Point it at the repository root instead and the install still appears to succeed,
but it copies a directory with no manifest and no `register(ctx)` in it: Hermes
warns that it "may not be a valid Hermes plugin", asks for nothing, and enables the
repository name, which nothing answers to. If you installed that way, remove
`~/.hermes/plugins/hermes-yandex-calendar` and install again with the directory
named.

### Option B — pip

```bash
pip install hermes-yandex-calendar
```

Hermes discovers it through the `hermes_agent.plugins` entry point; add
`yandex_calendar` to `plugins.enabled`.

### Option C — drop-in directory

Download `hermes-yandex-calendar-plugin-<version>.zip` from the
[latest release](https://github.com/akinfold/hermes-yandex-calendar/releases/latest)
— not the wheel, the `.tar.gz`, or GitHub's "Source code" archives — and unzip it
into `~/.hermes/plugins/` so you end up with
`~/.hermes/plugins/yandex_calendar/plugin.yaml`, then enable it the same way.

The archive holds the plugin directory alone, with no dependency metadata, so this
path installs nothing for you. `httpx` and `defusedxml` have to be importable in the
environment Hermes runs in: Hermes itself depends on `httpx`, but `defusedxml` is not
a core Hermes dependency, so if the plugin fails to load with `No module named
'defusedxml'`, run `pip install 'defusedxml>=0.7'` there. Option B installs both.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && ruff format --check .
pytest                       # unit tests, no network
```

## Running the live E2E tests

The `e2e`-marked tests hit a real Yandex account and are deselected by default.
They create, edit, move, and then delete throwaway events 400–402 days out, named
with the `hermes-e2e` prefix (`YC_E2E_MARKER` overrides it). Most land in the default
calendar; the move test also drops a short-lived probe event into your other
calendars, one at a time, until it finds one that accepts it, and then moves an event
there. Everything is deleted again, so a successful run leaves nothing behind — but
the attendees they invite do receive an invitation, so use addresses you own.

### Locally

```bash
YANDEX_CALENDAR_LOGIN=you@yandex.ru \
YANDEX_CALENDAR_APP_PASSWORD=xxxx \
YC_E2E_ATTENDEES=you+guest@yandex.ru \
pytest -m e2e
```

`YC_E2E_ATTENDEES` is the comma-separated list of addresses the throwaway event
invites, and each one really is emailed an invitation — so list mailboxes you own.
Omit it and the suite falls back to a `+e2e` sub-address of the account itself,
which lands in your own inbox. That fallback needs `YANDEX_CALENDAR_LOGIN` in full
e-mail form (`you@yandex.ru`); with a bare login there is nothing to derive it from
and the round-trip test fails asking for `YC_E2E_ATTENDEES`. Two constraints, both learned the hard way against
the live server:

- **The addresses must exist.** A made-up one (`guest@example.com`) bounces back
  into your mailbox.
- **They must not resolve to the account itself.** Yandex canonicalises addresses
  (`@ya.ru` → `@yandex.ru`) and drops an attendee that equals the `ORGANIZER`, so a
  plain alias of your own login silently disappears and the round-trip check fails.
  A `+tag` sub-address is delivered to the same mailbox but stays a distinct
  attendee.

Or keep all three out of the command line, in `~/.yandex-calendar-login`,
`~/.yandex-calendar-app-password`, and `~/.yandex-calendar-attendees`, and just run
`pytest -m e2e` — see `tests/e2e/conftest.py`.

The suite builds its client exactly as the plugin does, so every `YANDEX_CALENDAR_*`
setting it does not find in the environment (or, for the credentials, in those files)
is looked up in `~/.hermes/.env` — `YANDEX_CALENDAR_BASE_URL` and
`YANDEX_CALENDAR_CALENDARS` included. On a machine with Hermes already configured,
`pytest -m e2e` therefore runs against that account instead of skipping.

### On GitHub Actions

The **E2E (live)** workflow is manual (`workflow_dispatch`). It reads
`YANDEX_CALENDAR_LOGIN`, `YANDEX_CALENDAR_APP_PASSWORD`, and `YC_E2E_ATTENDEES`
from the **secrets** of a GitHub Environment named `yandex-calendar-e2e`. All three
must be secrets — the workflow reads nothing from environment variables, so a value
defined as a variable arrives empty: without the credentials every test is skipped,
and without `YC_E2E_ATTENDEES` the suite falls back to the `+e2e` sub-address. The
optional `install_hermes` input also runs `pip install hermes-agent` beforehand, on a
best-effort basis.

## Related Hermes plugins

Part of a family of Yandex plugins for Hermes Agent:

- [hermes-yandex-disk](https://github.com/akinfold/hermes-yandex-disk) — browse, read, write, and share files on Yandex Disk (REST API).
- [hermes-yandex-mail](https://github.com/akinfold/hermes-yandex-mail) — search, read, flag, move, and delete Yandex Mail messages over IMAP, and send over SMTP when sending is switched on.
- [hermes-yandex-search-api](https://github.com/akinfold/hermes-yandex-search-api) — Yandex web search backend and generative, cited answers for Hermes (Yandex Search API).

## Contributing

Issues and PRs are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the layout,
the plugin contract rules worth knowing, and the release process.

## License

MIT — see [LICENSE](LICENSE).
