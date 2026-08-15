"""Tests for the detection-driven `oh iterate init` wizard (v1.2-a)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

from openharness.iterate import config_loader, init_wizard


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    write(path, json.dumps(payload))


class TestDetectProject:
    def test_node_with_test_script_and_react(self, tmp_path):
        write_json(
            tmp_path / "package.json",
            {
                "scripts": {"test": "vitest run"},
                "dependencies": {"react": "^19.0.0"},
            },
        )
        profile = init_wizard.detect_project(tmp_path)
        assert profile.languages == ["node"]
        assert profile.test_command == "npm test"
        assert "frontend-backend" in profile.suggested_dimensions
        assert "ui-ux" in profile.suggested_dimensions
        assert any("npm test" in line for line in profile.evidence)

    def test_node_malformed_package_json_degrades_gracefully(self, tmp_path):
        write(tmp_path / "package.json", "{not json")
        profile = init_wizard.detect_project(tmp_path)
        assert profile.languages == ["node"]
        assert profile.test_command is None
        assert any("unreadable" in line for line in profile.evidence)

    def test_python_pyproject_with_pytest(self, tmp_path):
        write(tmp_path / "pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-q'\n")
        profile = init_wizard.detect_project(tmp_path)
        assert profile.languages == ["python"]
        assert profile.test_command == "pytest -q"

    def test_python_layout_fallback_without_pyproject(self, tmp_path):
        write(tmp_path / "requirements.txt", "flask\n")
        (tmp_path / "tests").mkdir()
        profile = init_wizard.detect_project(tmp_path)
        assert profile.languages == ["python"]
        assert profile.test_command == "pytest -q"
        assert "tests/ layout found" in " ".join(profile.evidence)

    def test_go_rust_java_php_markers(self, tmp_path):
        write(tmp_path / "go.mod", "module x\n")
        assert init_wizard.detect_project(tmp_path).test_command == "go test ./..."

        (tmp_path / "go.mod").unlink()
        write(tmp_path / "Cargo.toml", "[package]\nname = 'x'\n")
        assert init_wizard.detect_project(tmp_path).test_command == "cargo test"

        (tmp_path / "Cargo.toml").unlink()
        write(tmp_path / "pom.xml", "<project/>")
        assert init_wizard.detect_project(tmp_path).test_command == "mvn test"

        (tmp_path / "pom.xml").unlink()
        write_json(tmp_path / "composer.json", {"require": {}})
        assert init_wizard.detect_project(tmp_path).test_command == "composer test"

    def test_python_marker_deduped_across_multiple_files(self, tmp_path):
        write(tmp_path / "pyproject.toml", "[project]\nname = 'x'\n")
        write(tmp_path / "requirements.txt", "flask\n")
        profile = init_wizard.detect_project(tmp_path)
        assert profile.languages == ["python"]  # not ["python", "python"]

    def test_unknown_project_gets_base_dimensions(self, tmp_path):
        profile = init_wizard.detect_project(tmp_path)
        assert profile.is_unknown() is True
        assert profile.suggested_dimensions == list(init_wizard.BASE_DIMENSIONS)
        assert profile.test_command is None


class TestBuildAndRender:
    def test_build_config_dict_with_test_command(self):
        config = init_wizard.build_config_dict(
            goal="ship it",
            dimensions=["correctness", "security"],
            max_rounds=3,
            test_command="npm test",
        )
        assert config == {
            "goal": "ship it",
            "max_rounds": 3,
            "dimensions": ["correctness", "security"],
            "validation": {"commands": {"test": ["npm test"]}},
        }

    def test_build_config_dict_without_test_command_omits_validation(self):
        config = init_wizard.build_config_dict(
            goal="g", dimensions=["correctness"], max_rounds=2, test_command=None
        )
        assert "validation" not in config

    def test_render_is_injection_safe(self):
        evil = "goal: inject\nmax_rounds: 99\n"
        text = init_wizard.render_config_text(
            init_wizard.build_config_dict(
                goal=evil, dimensions=["security"], max_rounds=2, test_command=None
            )
        )
        parsed = yaml.safe_load(text)
        assert isinstance(parsed, dict)
        assert parsed["goal"] == evil  # stays a scalar string

    def test_write_config_roundtrips_through_loader(self, tmp_path):
        init_wizard.write_config(
            tmp_path,
            init_wizard.build_config_dict(
                goal="roundtrip",
                dimensions=["correctness", "security"],
                max_rounds=4,
                test_command="pytest -q",
            ),
        )
        path = init_wizard.existing_config_path(tmp_path)
        assert path.name == init_wizard.CONFIG_FILENAME
        effective = config_loader.load_effective_config(tmp_path)
        assert effective.source == "override"
        assert effective.config.goal == "roundtrip"
        assert effective.config.dimensions == ["correctness", "security"]
        assert effective.config.validation.commands["test"] == ["pytest -q"]


class TestParseDimensionSelection:
    OFFERED: ClassVar[list[str]] = ["correctness", "security", "performance", "ui-ux"]

    def test_empty_keeps_all(self):
        assert init_wizard.parse_dimension_selection("", self.OFFERED) == self.OFFERED
        assert init_wizard.parse_dimension_selection("   ", self.OFFERED) == self.OFFERED

    def test_indexes_and_names(self):
        assert init_wizard.parse_dimension_selection("2,4", self.OFFERED) == ["security", "ui-ux"]
        assert init_wizard.parse_dimension_selection("security ui-ux", self.OFFERED) == [
            "security",
            "ui-ux",
        ]

    def test_underscore_name_normalizes_to_hyphen(self):
        assert init_wizard.parse_dimension_selection("ui_ux", self.OFFERED) == ["ui-ux"]

    def test_duplicates_collapse_in_offered_order(self):
        assert init_wizard.parse_dimension_selection("1 security 1", self.OFFERED) == [
            "correctness",
            "security",
        ]

    @pytest.mark.parametrize("raw", ["0", "9", "nonsense", "1,banana"])
    def test_invalid_inputs_return_none(self, raw):
        assert init_wizard.parse_dimension_selection(raw, self.OFFERED) is None
