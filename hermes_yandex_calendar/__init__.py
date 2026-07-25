"""Yandex Calendar plugin for Hermes Agent.

Registers three standalone tools that talk to Yandex Calendar over CalDAV:
list, create, and delete events. Uses RELATIVE imports so it loads both as a
dropped-in directory plugin (``hermes_plugins.<slug>``) and as a pip package.
"""

from __future__ import annotations

from typing import Any

from . import tool
from .config import ENV_LOGIN, ENV_PASSWORD, credentials_present

__version__ = "0.1.0"

__all__ = ["__version__", "register"]

_REQUIRES_ENV = [ENV_LOGIN, ENV_PASSWORD]


def register(ctx: Any) -> None:
    """Called by Hermes at load time with a PluginContext."""
    ctx.register_tool(
        name=tool.LIST_CALENDARS_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.LIST_CALENDARS_SCHEMA,
        handler=tool.handle_list_calendars,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="List the available Yandex calendars.",
        emoji="📇",
    )
    ctx.register_tool(
        name=tool.LIST_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.LIST_SCHEMA,
        handler=tool.handle_list,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="List events from Yandex Calendar in a time range.",
        emoji="📅",
    )
    ctx.register_tool(
        name=tool.CREATE_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.CREATE_SCHEMA,
        handler=tool.handle_create,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="Create an event in Yandex Calendar.",
        emoji="📅",
    )
    ctx.register_tool(
        name=tool.UPDATE_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.UPDATE_SCHEMA,
        handler=tool.handle_update,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="Update an event (attendees, busy status, times) in Yandex Calendar.",
        emoji="✏️",
    )
    ctx.register_tool(
        name=tool.RESPOND_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.RESPOND_SCHEMA,
        handler=tool.handle_respond,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="Accept, decline, or tentatively accept a Yandex Calendar invitation.",
        emoji="✅",
    )
    ctx.register_tool(
        name=tool.MOVE_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.MOVE_SCHEMA,
        handler=tool.handle_move,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="Move a Yandex Calendar event to another calendar.",
        emoji="📤",
    )
    ctx.register_tool(
        name=tool.DELETE_SCHEMA["name"],
        toolset=tool.TOOLSET,
        schema=tool.DELETE_SCHEMA,
        handler=tool.handle_delete,
        check_fn=credentials_present,
        requires_env=_REQUIRES_ENV,
        description="Delete an event from Yandex Calendar by href.",
        emoji="🗑️",
    )
