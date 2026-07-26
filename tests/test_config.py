"""Unit tests for credential resolution and client construction."""

from __future__ import annotations

import pytest

from hermes_yandex_calendar import config
from hermes_yandex_calendar._compat import get_provider_env


def test_credentials_present_false_when_unset(monkeypatch):
    monkeypatch.delenv(config.ENV_LOGIN, raising=False)
    monkeypatch.delenv(config.ENV_PASSWORD, raising=False)
    # ensure the ~/.hermes/.env fallback can't accidentally supply creds
    monkeypatch.setattr(config, "get_provider_env", lambda name: "")
    assert config.credentials_present() is False


def test_credentials_present_true(monkeypatch):
    monkeypatch.setattr(
        config,
        "get_provider_env",
        lambda name: "x" if "LOGIN" in name or "PASSWORD" in name else "",
    )
    assert config.credentials_present() is True


def test_build_client_raises_without_creds(monkeypatch):
    monkeypatch.setattr(config, "get_provider_env", lambda name: "")
    with pytest.raises(config.MissingCredentials):
        config.build_client()


def test_build_client_uses_base_url(monkeypatch):
    values = {
        config.ENV_LOGIN: "user@yandex.ru",
        config.ENV_PASSWORD: "pw",
        config.ENV_BASE_URL: "https://caldav.example.test",
    }
    monkeypatch.setattr(config, "get_provider_env", lambda name: values.get(name, ""))
    client = config.build_client()
    try:
        assert client.base_url == "https://caldav.example.test"
        assert client.login == "user@yandex.ru"
    finally:
        client.close()


def test_get_provider_env_reads_environ(monkeypatch):
    monkeypatch.setenv("YANDEX_CALENDAR_TEST_KEY", "  value  ")
    assert get_provider_env("YANDEX_CALENDAR_TEST_KEY") == "value"


def test_allowed_calendars_parses_csv(monkeypatch):
    monkeypatch.setattr(
        config,
        "get_provider_env",
        lambda name: " Work , Personal ,, " if "CALENDARS" in name else "",
    )
    assert config.allowed_calendars() == ["Work", "Personal"]


def test_allowed_calendars_empty(monkeypatch):
    monkeypatch.setattr(config, "get_provider_env", lambda name: "")
    assert config.allowed_calendars() == []


def test_build_client_passes_allow_list(monkeypatch):
    values = {
        config.ENV_LOGIN: "user@yandex.ru",
        config.ENV_PASSWORD: "pw",
        config.ENV_CALENDARS: "Work,Personal",
    }
    monkeypatch.setattr(config, "get_provider_env", lambda name: values.get(name, ""))
    client = config.build_client()
    try:
        assert client._allowed_raw == ["Work", "Personal"]
    finally:
        client.close()


def _with_actions(monkeypatch, raw: str) -> None:
    monkeypatch.setattr(
        config,
        "get_provider_env",
        lambda name: raw if name == config.ENV_ACTIONS else "",
    )


@pytest.mark.parametrize("raw", ["", "   "])
def test_allowed_actions_defaults_to_everything(monkeypatch, raw):
    _with_actions(monkeypatch, raw)
    assert config.allowed_actions() == frozenset(config.ACTIONS)


def test_allowed_actions_separators_only_allow_nothing(monkeypatch):
    # A set-but-meaningless value is still an explicit allow-list: grant nothing.
    _with_actions(monkeypatch, ",, ,")
    assert config.allowed_actions() == frozenset()


def test_allowed_actions_single_actions(monkeypatch):
    _with_actions(monkeypatch, " list_events , create_event ")
    assert config.allowed_actions() == {"list_events", "create_event"}


def test_allowed_actions_groups(monkeypatch):
    _with_actions(monkeypatch, "read,delete")
    assert config.allowed_actions() == {"list_calendars", "list_events", "delete_event"}


def test_allowed_actions_accepts_tool_names_and_dashes(monkeypatch):
    _with_actions(monkeypatch, "yandex_calendar_delete_event,LIST-EVENTS")
    assert config.allowed_actions() == {"delete_event", "list_events"}


def test_allowed_actions_drops_unknown_names(monkeypatch):
    # A typo must never widen access — it can only withhold a tool.
    _with_actions(monkeypatch, "list_events,delet_event,../../etc/passwd")
    assert config.allowed_actions() == {"list_events"}


def test_get_provider_env_reads_hermes_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("YC_FROM_FILE", raising=False)
    hermes_dir = tmp_path / ".hermes"
    hermes_dir.mkdir()
    (hermes_dir / ".env").write_text('YC_FROM_FILE="secret"\n# comment\nOTHER=1\n')
    monkeypatch.setattr("hermes_yandex_calendar._compat.Path.home", lambda: tmp_path)
    assert get_provider_env("YC_FROM_FILE") == "secret"
