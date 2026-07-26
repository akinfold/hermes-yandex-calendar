"""Yandex Calendar plugin for Hermes Agent.

Registers standalone tools that talk to Yandex Calendar over CalDAV: list,
create, update, respond to, move, and delete events. Which of them appear is
governed by ``YANDEX_CALENDAR_ACTIONS`` (see :func:`config.allowed_actions`).
Uses RELATIVE imports so it loads both as a dropped-in directory plugin
(``hermes_plugins.<slug>``) and as a pip package.
"""

from __future__ import annotations

from typing import Any

from . import tool
from .config import ENV_LOGIN, ENV_PASSWORD, allowed_actions, credentials_present

__version__ = "0.2.1"

__all__ = ["__version__", "register"]

_REQUIRES_ENV = [ENV_LOGIN, ENV_PASSWORD]

# (action, schema, handler, description, emoji) — the action names are the ones
# YANDEX_CALENDAR_ACTIONS accepts.
_TOOLS: tuple[tuple[str, dict, Any, str, str], ...] = (
    (
        "list_calendars",
        tool.LIST_CALENDARS_SCHEMA,
        tool.handle_list_calendars,
        "List the available Yandex calendars.",
        "📇",
    ),
    (
        "list_events",
        tool.LIST_SCHEMA,
        tool.handle_list,
        "List events from Yandex Calendar in a time range.",
        "📅",
    ),
    (
        "create_event",
        tool.CREATE_SCHEMA,
        tool.handle_create,
        "Create an event in Yandex Calendar.",
        "📅",
    ),
    (
        "update_event",
        tool.UPDATE_SCHEMA,
        tool.handle_update,
        "Update an event (attendees, busy status, times) in Yandex Calendar.",
        "✏️",
    ),
    (
        "respond_event",
        tool.RESPOND_SCHEMA,
        tool.handle_respond,
        "Accept, decline, or tentatively accept a Yandex Calendar invitation.",
        "✅",
    ),
    (
        "move_event",
        tool.MOVE_SCHEMA,
        tool.handle_move,
        "Move a Yandex Calendar event to another calendar.",
        "📤",
    ),
    (
        "delete_event",
        tool.DELETE_SCHEMA,
        tool.handle_delete,
        "Delete an event from Yandex Calendar by href.",
        "🗑️",
    ),
)


def register(ctx: Any) -> None:
    """Called by Hermes at load time with a PluginContext.

    Only the tools whose action is allowed are registered, so a disallowed action
    is not merely refused — the agent never sees the tool at all.
    """
    allowed = allowed_actions()
    for action, schema, handler, description, emoji in _TOOLS:
        if action not in allowed:
            continue
        ctx.register_tool(
            name=schema["name"],
            toolset=tool.TOOLSET,
            schema=schema,
            handler=handler,
            check_fn=credentials_present,
            requires_env=_REQUIRES_ENV,
            description=description,
            emoji=emoji,
        )
