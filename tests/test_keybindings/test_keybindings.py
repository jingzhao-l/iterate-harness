"""Unit tests for the keybindings subsystem.

Covers the JSON parser (happy path + validation errors), the default-map
resolver (merge semantics), and the config-file loader (missing / valid /
invalid files), all against an isolated ``ITERATE_CONFIG_DIR``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iterate_harness.keybindings.default_bindings import DEFAULT_KEYBINDINGS
from iterate_harness.keybindings.loader import get_keybindings_path, load_keybindings
from iterate_harness.keybindings.parser import parse_keybindings
from iterate_harness.keybindings.resolver import resolve_keybindings


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch):
    """Point the config dir at a temp dir so loader tests never touch ~."""
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(tmp_path))


# ---------------------------------------------------------------------------
# default_bindings
# ---------------------------------------------------------------------------


def test_default_bindings_contain_expected_map():
    assert DEFAULT_KEYBINDINGS == {
        "ctrl+l": "clear",
        "ctrl+k": "toggle_vim",
        "ctrl+v": "toggle_voice",
        "ctrl+t": "tasks",
    }


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def test_parse_valid_object():
    assert parse_keybindings('{"ctrl+x": "quit"}') == {"ctrl+x": "quit"}


def test_parse_empty_object():
    assert parse_keybindings("{}") == {}


def test_parse_preserves_multiple_entries():
    assert parse_keybindings('{"a": "1", "b": "2"}') == {"a": "1", "b": "2"}


def test_parse_rejects_non_object():
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_keybindings("[1, 2]")


def test_parse_rejects_non_string_value():
    with pytest.raises(ValueError, match="keys and values must be strings"):
        parse_keybindings('{"ctrl+x": 42}')


def test_parse_rejects_invalid_json():
    with pytest.raises(json.JSONDecodeError):
        parse_keybindings("not json at all")


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


def test_resolve_returns_defaults_without_overrides():
    assert resolve_keybindings() == DEFAULT_KEYBINDINGS


def test_resolve_empty_overrides_returns_defaults():
    assert resolve_keybindings({}) == DEFAULT_KEYBINDINGS


def test_resolve_merges_overrides_over_defaults():
    resolved = resolve_keybindings({"ctrl+l": "other"})
    assert resolved["ctrl+l"] == "other"
    # Unrelated defaults survive the merge.
    assert resolved["ctrl+t"] == "tasks"


def test_resolve_does_not_mutate_shared_defaults():
    overrides = {"ctrl+l": "other"}
    resolve_keybindings(overrides)
    assert DEFAULT_KEYBINDINGS["ctrl+l"] == "clear"


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def test_get_keybindings_path_uses_config_dir(tmp_path: Path):
    assert get_keybindings_path() == tmp_path / "keybindings.json"


def test_load_returns_defaults_when_no_file():
    assert load_keybindings() == DEFAULT_KEYBINDINGS


def test_load_merges_user_overrides(tmp_path: Path):
    (tmp_path / "keybindings.json").write_text(
        json.dumps({"ctrl+l": "quit"}),
        encoding="utf-8",
    )
    resolved = load_keybindings()
    assert resolved["ctrl+l"] == "quit"
    assert resolved["ctrl+t"] == "tasks"


def test_load_raises_on_invalid_file(tmp_path: Path):
    (tmp_path / "keybindings.json").write_text("[broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_keybindings()
