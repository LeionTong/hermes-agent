"""``hermes providers list``: the scriptable provider inventory and its ``--offline`` guarantee."""

from __future__ import annotations

import argparse
import json
import socket

import pytest

from hermes_cli.inventory import ConfigContext
from hermes_cli.inventory_offline import build_offline_models_payload
from hermes_cli.subcommands.providers import build_providers_parser


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hermes")
    build_providers_parser(parser.add_subparsers(dest="command"))
    return parser


def _ctx(**overrides) -> ConfigContext:
    base = dict(current_provider="", current_model="", current_base_url="", user_providers={}, custom_providers=[])
    base.update(overrides)
    return ConfigContext(**base)


def test_parser_accepts_scriptable_offline_flags():
    args = _parser().parse_args(["providers", "ls", "--all", "--no-live", "--json", "--provider", "openrouter"])
    assert (args.providers_action, args.offline, args.all, args.json, args.provider) == (
        "ls", True, True, True, "openrouter")


def test_offline_inventory_never_opens_a_socket(monkeypatch):
    """--offline must hold with every provider listed: a connect attempt anywhere is a failure."""
    def _refuse(*_a, **_kw):
        raise AssertionError("--offline opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    payload = build_offline_models_payload(_ctx(current_provider="openrouter"), include_unconfigured=True)
    assert any(row["slug"] == "anthropic" for row in payload["providers"])


def test_offline_inventory_uses_config_and_bundled_catalogs():
    ctx = _ctx(
        current_provider="openrouter", current_model="example/current-model",
        user_providers={"local": {"name": "Local gateway", "models": {"qwen-local": {}}}},
        custom_providers=[{"name": "Legacy endpoint", "base_url": "http://localhost:8000/v1",
                           "models": ["legacy-model"]}],
    )
    by_id = {row["slug"]: row for row in build_offline_models_payload(ctx)["providers"]}
    assert {"openrouter", "local", "custom:legacy-endpoint"} <= set(by_id)
    assert by_id["openrouter"]["is_current"] is True
    assert by_id["openrouter"]["models"][0] == "example/current-model"
    assert by_id["local"]["models"] == ["qwen-local"]
    assert by_id["custom:legacy-endpoint"]["models"] == ["legacy-model"]


@pytest.mark.parametrize("legacy_override", [
    {"key_env": "LEGACY_API_KEY"}, {"api_mode": "responses"}, {"extra_headers": {"X-Tenant": "legacy"}},
], ids=["credential", "api-mode", "headers"])
def test_offline_inventory_keeps_legacy_entries_with_distinct_endpoint_identity(legacy_override):
    shared = {"base_url": "https://gateway.example.test/v1", "key_env": "KEYED_API_KEY",
              "api_mode": "chat_completions", "extra_headers": {"X-Tenant": "keyed"}}
    legacy = {**shared, "name": "Legacy endpoint", "models": ["legacy-model"], **legacy_override}
    ctx = _ctx(user_providers={"configured": {**shared, "name": "Configured", "models": ["configured-model"]}},
               custom_providers=[legacy])
    by_id = {row["slug"]: row for row in build_offline_models_payload(ctx)["providers"]}
    assert by_id["configured"]["models"] == ["configured-model"]
    assert by_id["custom:legacy-endpoint"]["models"] == ["legacy-model"]


def _run(monkeypatch, capsys, argv):
    monkeypatch.setattr("hermes_cli.inventory.load_picker_context", lambda: _ctx(
        current_provider="local", current_model="m1",
        user_providers={"local": {"name": "Local", "models": ["m1", "m2"], "key_env": "LOCAL_KEY"}}))
    args = _parser().parse_args(argv)
    code = args.func(args)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_json_document_shape(monkeypatch, capsys):
    code, out, err = _run(monkeypatch, capsys, ["providers", "list", "--offline", "--json", "--provider", "local"])
    doc = json.loads(out)
    assert code == 0 and err == ""
    assert list(doc)[0] == "schema_version" and doc["schema_version"] == 1
    assert doc["offline"] is True and doc["current"] == {"provider": "local", "model": "m1"}
    assert doc["providers"] == [{
        "id": "local", "name": "Local", "auth_state": "configured", "auth_type": "api_key",
        "env_vars": ["LOCAL_KEY"], "is_current": True, "is_user_defined": True,
        "models": ["m1", "m2"], "model_count": 2, "source": "user-config",
    }]


def test_unknown_provider_errors_on_stderr_with_empty_stdout(monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, capsys, ["providers", "list", "--offline", "--json", "--provider", "nope"])
    out = capsys.readouterr()
    assert exc.value.code == 2
    assert out.out == "" and "unknown or unavailable provider: nope" in out.err
