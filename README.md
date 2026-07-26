# hermes-yandex-calendar

[![CI](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml/badge.svg)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml)
[![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/akinfold/hermes-yandex-calendar/badges/coverage.json)](https://github.com/akinfold/hermes-yandex-calendar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Give [Hermes Agent](https://github.com/NousResearch/hermes-agent) read/write access to your Yandex Calendar** — list, create, update, respond to, move, and delete events over CalDAV, across multiple calendars, with attendee and busy/free management.

Tested against Hermes **0.19.x**, Python **3.11–3.13**.

## What it does

Registers up to seven standalone tools — you decide which ones the agent gets, see
[Restricting what the agent can do](#restricting-what-the-agent-can-do):

| Tool | Purpose |
|---|---|
| `yandex_calendar_list_calendars` | List the calendars the plugin can use (name + `href`). |
| `yandex_calendar_list_events` | List events in a time range from a calendar (returns summary, start/end, location, description, attendees, busy status, and an `href`). |
| `yandex_calendar_create_event` | Create an event (summary, start, optional end/location/description/all-day, attendees, busy status, target calendar). |
| `yandex_calendar_update_event` | Edit an existing event by `href`: change fields, add/remove attendees, toggle busy/free. Recurrence rules and alarms are preserved. |
| `yandex_calendar_respond_event` | Respond to a meeting invitation — accept, decline, or tentatively accept. |
| `yandex_calendar_move_event` | Move an event to another calendar (details, attendees, recurrence, and alarms are kept). |
| `yandex_calendar_delete_event` | Delete an event by the `href` returned from a list. |

Yandex Calendar has no public REST API, so this plugin speaks **CalDAV**
(`https://caldav.yandex.ru`) directly — the same protocol Yandex's own docs point
third-party clients at.

### Multiple calendars

Every tool that reads or writes events takes an optional `calendar` argument — a
calendar **name** (as returned by `yandex_calendar_list_calendars`) or its `href`.
Omit it to use the default (first) calendar. `update` and `delete` identify the
event by its `href`, which already encodes the calendar it lives in.

You can restrict which calendars the plugin touches with `YANDEX_CALENDAR_CALENDARS`
(see below); the first one in that list becomes the default.

## Quick start

1. **Create a Yandex app password** (a normal account password will not work for
   CalDAV): https://id.yandex.ru/security/app-passwords → add a password scoped to
   *"Calendar (CalDAV)"*.

2. **Install the plugin into Hermes:**

   ```bash
   hermes plugins install akinfold/hermes-yandex-calendar --enable
   ```

3. **Provide credentials** — either answer the install prompts, or add them to
   `~/.hermes/.env`:

   ```dotenv
   YANDEX_CALENDAR_LOGIN=you@yandex.ru
   YANDEX_CALENDAR_APP_PASSWORD=xxxxxxxxxxxxxxxx
   ```

4. **Enable it** in `~/.hermes/config.yaml` (third-party plugins are off by default):

   ```yaml
   plugins:
     enabled: [yandex_calendar]
   ```

That's it — ask the agent things like *"what's on my calendar next week?"* or
*"add a 30-minute call tomorrow at 3pm"*.

Not comfortable handing over write access yet? Add `YANDEX_CALENDAR_ACTIONS=read`
and the agent can only look — see
[Restricting what the agent can do](#restricting-what-the-agent-can-do).

## Configuration

| Env var | Required | Default | Meaning |
|---|---|---|---|
| `YANDEX_CALENDAR_LOGIN` | yes | — | Yandex login / email. |
| `YANDEX_CALENDAR_APP_PASSWORD` | yes | — | App password for CalDAV (see step 1). |
| `YANDEX_CALENDAR_BASE_URL` | no | `https://caldav.yandex.ru` | Override for self-hosted / testing. |
| `YANDEX_CALENDAR_CALENDARS` | no | *(all)* | Comma-separated allow-list of calendar names the plugin may use, e.g. `Work,Personal`. The first is the default calendar. |
| `YANDEX_CALENDAR_ACTIONS` | no | *(all)* | Comma-separated allow-list of actions the agent may perform — see below. |

### Restricting what the agent can do

`YANDEX_CALENDAR_ACTIONS` decides which of the seven tools are registered at all.
A disallowed action is not merely refused at call time: the tool never appears in
the agent's toolset, so it cannot be invoked, and the model is not tempted to try.

Accepted values, comma-separated and case-insensitive — individual actions
(`list_calendars`, `list_events`, `create_event`, `update_event`, `respond_event`,
`move_event`, `delete_event`) or the shorthands:

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

Full tool names work too, so you can paste them straight from the table above
(`yandex_calendar_delete_event`).

Leave it unset for all seven tools. A name that matches nothing is ignored, so a
typo can only ever withhold a tool, never grant one — and a value that names
nothing recognisable therefore registers nothing at all. The list is applied when
the plugin loads — restart Hermes after changing it.

Credentials are read from the environment first, then from `~/.hermes/.env`, so they
work in gateway and subprocess runs. Secret values are never logged.

Dates and times are ISO 8601 (`2026-07-25` or `2026-07-25T14:00:00+03:00`); a datetime
without an offset is treated as UTC.

## Install alternatives

- **pip** (discovered via the `hermes_agent.plugins` entry point):
  ```bash
  pip install hermes-yandex-calendar
  ```
  then add `yandex_calendar` to `plugins.enabled`.
- **Drop-in**: unzip the release archive into `~/.hermes/plugins/` so you get
  `~/.hermes/plugins/yandex_calendar/plugin.yaml`, then enable it.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
ruff check . && ruff format --check .
pytest                       # unit tests (no network)
```

Live end-to-end tests hit a real account and are deselected by default. They create
then delete a throwaway event far in the future, so a successful run leaves no residue:

```bash
YANDEX_CALENDAR_LOGIN=you@yandex.ru \
YANDEX_CALENDAR_APP_PASSWORD=xxxx \
pytest -m e2e
```

## License

MIT — see [LICENSE](LICENSE).
