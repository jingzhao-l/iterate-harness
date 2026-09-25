"""Tests for the lightweight LSP code-intelligence helpers."""

from __future__ import annotations

from pathlib import Path

from iterate_harness.services.lsp import (
    extract_symbol_at_position,
    find_references,
    go_to_definition,
)

# "def greet(name):" — 1-based character map:
#   d(1) e(2) f(3) space(4) g(5) r(6) e(7) e(8) t(9) ((10) n(11) a(12) m(13) e(14) )(15) :(16)
LINE_1 = "def greet(name):"


class TestExtractSymbolAtPosition:
    def test_inside_identifier(self, tmp_path: Path):
        app = tmp_path / "app.py"
        app.write_text(LINE_1, encoding="utf-8")
        assert extract_symbol_at_position(app, line=1, character=5) == "greet"
        assert extract_symbol_at_position(app, line=1, character=9) == "greet"
        assert extract_symbol_at_position(app, line=1, character=11) == "name"

    def test_snap_back_after_identifier(self, tmp_path: Path):
        app = tmp_path / "app.py"
        app.write_text("x = foo_bar * baz_qux", encoding="utf-8")
        # Cursor one char past "foo_bar" (its end is index 11, char 12).
        assert extract_symbol_at_position(app, line=1, character=12) == "foo_bar"
        # Cursor between identifiers picks the closer following one.
        assert extract_symbol_at_position(app, line=1, character=13) == "baz_qux"

    def test_out_of_range_and_empty(self, tmp_path: Path):
        app = tmp_path / "app.py"
        app.write_text(LINE_1, encoding="utf-8")
        assert extract_symbol_at_position(app, line=0, character=1) is None
        assert extract_symbol_at_position(app, line=99, character=1) is None
        app.write_text("!!!", encoding="utf-8")
        assert extract_symbol_at_position(app, line=1, character=2) is None

    def test_defaults_to_start_of_line(self, tmp_path: Path):
        app = tmp_path / "app.py"
        app.write_text(LINE_1, encoding="utf-8")
        assert extract_symbol_at_position(app, line=1, character=None) == "def"


class TestGoToDefinitionAndReferences:
    def test_resolves_across_workspace(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "utils.py").write_text(
            'def greet(name):\n    """Return a greeting."""\n    return f"hi {name}"\n',
            encoding="utf-8",
        )
        app = tmp_path / "pkg" / "app.py"
        app.write_text("from pkg.utils import greet\nprint(greet('world'))\n", encoding="utf-8")

        definitions = go_to_definition(root=tmp_path, file_path=app, symbol="greet")
        assert definitions and definitions[0].name == "greet"
        assert str(definitions[0].path).endswith("pkg/utils.py")

        refs = find_references(root=tmp_path, file_path=app, symbol="greet")
        assert any(str(path).endswith("pkg/app.py") for path, _, _ in refs)