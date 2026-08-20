"""Unit tests for the themes subsystem.

Covers the pydantic schema (defaults + validation), the built-in theme
catalog (integrity), and the loader (custom-theme discovery, invalid-file
skipping, precedence, and unknown-name errors).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from iterate_harness.themes.builtin import BUILTIN_THEMES
from iterate_harness.themes.loader import (
    get_custom_themes_dir,
    list_themes,
    load_custom_themes,
    load_theme,
)
from iterate_harness.themes.schema import (
    BorderConfig,
    ColorsConfig,
    IconConfig,
    LayoutConfig,
    ThemeConfig,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch):
    """Redirect Path.home() to a temp dir so custom themes never touch ~."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))


def _write_theme(tmp_path: Path, name: str, data: dict) -> Path:
    path = get_custom_themes_dir() / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def test_colors_defaults():
    colors = ColorsConfig()
    assert colors.primary == "#5875d4"
    assert colors.error == "#e06c75"
    assert colors.background == "#282c34"


def test_border_defaults():
    assert BorderConfig().style == "rounded"
    assert BorderConfig().char is None


def test_border_rejects_unknown_style():
    with pytest.raises(ValidationError):
        BorderConfig(style="squircle")


def test_icon_defaults():
    assert IconConfig().spinner == "⠋"
    assert IconConfig().success == "✔"


def test_layout_defaults():
    layout = LayoutConfig()
    assert layout.compact is False
    assert layout.show_tokens is True
    assert layout.show_time is True


def test_theme_config_requires_name():
    with pytest.raises(ValidationError):
        ThemeConfig()


def test_theme_config_partial_overrides_keep_defaults():
    theme = ThemeConfig(name="t", colors=ColorsConfig(primary="#000000"))
    assert theme.colors.primary == "#000000"
    assert theme.colors.background == "#282c34"
    assert theme.layout.compact is False


# ---------------------------------------------------------------------------
# builtin
# ---------------------------------------------------------------------------


def test_builtin_theme_names_match_keys():
    assert set(BUILTIN_THEMES) == {
        "default",
        "dark",
        "minimal",
        "cyberpunk",
        "solarized",
    }
    for name, theme in BUILTIN_THEMES.items():
        assert theme.name == name


def test_builtin_themes_have_distinct_primary_colors():
    primaries = {theme.colors.primary for theme in BUILTIN_THEMES.values()}
    assert len(primaries) == len(BUILTIN_THEMES)


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def test_load_custom_themes_empty_in_fresh_home():
    assert load_custom_themes() == {}


def test_load_custom_theme_from_file(tmp_path: Path):
    _write_theme(tmp_path, "mine", {"name": "mine", "colors": {"primary": "#123456"}})
    themes = load_custom_themes()
    assert set(themes) == {"mine"}
    assert themes["mine"].colors.primary == "#123456"


def test_invalid_custom_theme_is_skipped(tmp_path: Path):
    _write_theme(tmp_path, "bad", {"name": "bad", "borders": {"style": "squircle"}})
    _write_theme(tmp_path, "good", {"name": "good"})
    themes = load_custom_themes()
    assert set(themes) == {"good"}


def test_invalid_json_custom_theme_is_skipped(tmp_path: Path):
    (get_custom_themes_dir() / "broken.json").write_text("{ nope ", encoding="utf-8")
    assert load_custom_themes() == {}


def test_list_themes_contains_builtin_and_custom(tmp_path: Path):
    _write_theme(tmp_path, "mine", {"name": "mine"})
    names = list_themes()
    assert "default" in names
    assert "cyberpunk" in names
    assert "mine" in names


def test_list_themes_deduplicates_custom_name(tmp_path: Path):
    _write_theme(tmp_path, "default", {"name": "default", "colors": {"primary": "#111111"}})
    names = list_themes()
    assert names.count("default") == 1


def test_load_theme_returns_builtin():
    theme = load_theme("cyberpunk")
    assert theme.name == "cyberpunk"
    assert theme.borders.style == "double"


def test_load_theme_prefers_custom_over_builtin(tmp_path: Path):
    _write_theme(tmp_path, "default", {"name": "default", "colors": {"primary": "#111111"}})
    assert load_theme("default").colors.primary == "#111111"


def test_load_theme_unknown_raises():
    with pytest.raises(KeyError, match="Unknown theme"):
        load_theme("does-not-exist")
