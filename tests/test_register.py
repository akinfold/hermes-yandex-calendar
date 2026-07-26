"""Tests for register(): which tools reach Hermes, given YANDEX_CALENDAR_ACTIONS."""

from __future__ import annotations

import pytest

import hermes_yandex_calendar as plugin
from hermes_yandex_calendar import config


class FakeCtx:
    def __init__(self) -> None:
        self.tools: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.tools.append(kwargs)


def _register_with(monkeypatch, actions: str) -> list[str]:
    monkeypatch.setattr(
        config,
        "get_provider_env",
        lambda name: actions if name == config.ENV_ACTIONS else "",
    )
    ctx = FakeCtx()
    plugin.register(ctx)
    return [t["name"] for t in ctx.tools]


def test_registers_every_tool_by_default(monkeypatch):
    names = _register_with(monkeypatch, "")
    assert names == [f"yandex_calendar_{action}" for action in config.ACTIONS]


def test_read_only_deployment(monkeypatch):
    names = _register_with(monkeypatch, "read")
    assert names == ["yandex_calendar_list_calendars", "yandex_calendar_list_events"]


def test_everything_but_delete(monkeypatch):
    names = _register_with(monkeypatch, "read,write")
    assert "yandex_calendar_delete_event" not in names
    assert len(names) == 6


def test_single_action(monkeypatch):
    assert _register_with(monkeypatch, "create_event") == ["yandex_calendar_create_event"]


def test_unknown_action_registers_nothing(monkeypatch):
    assert _register_with(monkeypatch, "nonsense") == []


@pytest.mark.parametrize("field", ["schema", "handler", "check_fn", "requires_env"])
def test_registration_payload(monkeypatch, field):
    monkeypatch.setattr(config, "get_provider_env", lambda name: "")
    ctx = FakeCtx()
    plugin.register(ctx)
    assert all(t[field] for t in ctx.tools)
    for t in ctx.tools:
        assert t["toolset"] == "yandex_calendar"
        assert t["schema"]["name"] == t["name"]
