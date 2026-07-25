"""Simulate Hermes' directory-plugin loader and prove register() works.

This mirrors how Hermes loads a dropped-in plugin: it imports the directory as
``hermes_plugins.<slug>`` via ``spec_from_file_location`` with
``submodule_search_locations`` set, then calls ``getattr(module, "register")``.
Proves the relative imports in ``__init__.py`` resolve without a full install.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "hermes_yandex_calendar"


class FakeCtx:
    def __init__(self):
        self.tools: list[dict] = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_directory_loader_registers_tools():
    ns = "hermes_plugins"
    sys.modules.setdefault(ns, types.ModuleType(ns)).__path__ = []  # type: ignore[attr-defined]
    mod_name = f"{ns}.yandex_calendar"
    spec = importlib.util.spec_from_file_location(
        mod_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = mod_name
    module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)

    ctx = FakeCtx()
    module.register(ctx)

    names = {t["name"] for t in ctx.tools}
    assert names == {
        "yandex_calendar_list_calendars",
        "yandex_calendar_list_events",
        "yandex_calendar_create_event",
        "yandex_calendar_update_event",
        "yandex_calendar_respond_event",
        "yandex_calendar_move_event",
        "yandex_calendar_delete_event",
    }
    for t in ctx.tools:
        assert t["toolset"] == "yandex_calendar"
        assert callable(t["handler"])
        assert t["schema"]["name"] == t["name"]
