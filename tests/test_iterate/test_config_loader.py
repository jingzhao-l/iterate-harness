"""Tests for openharness.iterate.config_loader (port of config-loader.test.ts)."""

from __future__ import annotations

from pathlib import Path

from openharness.iterate.config_loader import (
    default_config,
    flatten_commands,
    is_command_allowed,
    load_config,
    load_effective_config,
    merge_config,
    validate_config,
)


def make_temp_dir(tmp_path: Path) -> Path:
    return tmp_path / "project"


def write_config(dir_path: Path, content: str) -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / "iterate.config.yaml").write_text(content, encoding="utf-8")


class TestDefaultConfig:
    def test_provides_every_required_field_with_sensible_defaults(self):
        c = default_config()
        assert len(c.goal) > 0
        assert isinstance(c.max_rounds, int)
        assert c.language in ("zh", "en")
        assert isinstance(c.dimensions, list) and len(c.dimensions) > 0
        assert c.review.scope == "full"
        assert c.atomic.max_lines == 20
        assert c.atomic.max_adjacent_methods == 3
        assert c.git.target_branch == "main"
        assert c.git.use_worktree is False
        assert c.git.push_per_round is False
        assert c.git.auto_merge is False
        # Security: defaults configure NO trusted validation commands.
        assert c.validation.command_whitelist == []
        assert c.validation.commands == {}
        assert c.reviewer.output_schema_validation is True


class TestMergeConfig:
    def test_fills_missing_keys_from_base_without_mutating_inputs(self):
        base = {"goal": "g", "atomic": {"max_lines": 20, "max_adjacent_methods": 3}}
        override = {"goal": "new goal"}
        merged = merge_config(base, override)
        assert merged["goal"] == "new goal"
        assert merged["atomic"] == {"max_lines": 20, "max_adjacent_methods": 3}
        # The base input was not mutated.
        assert base["goal"] == "g"
        assert base["atomic"]["max_lines"] == 20

    def test_merges_nested_objects_recursively_arrays_replaced_wholesale(self):
        base = {
            "atomic": {"max_lines": 20, "max_adjacent_methods": 3},
            "dimensions": ["a", "b"],
            "validation": {
                "command_whitelist": ["pytest"],
                "commands": {"python": ["pytest tests/"]},
            },
        }
        override = {
            "atomic": {"max_lines": 50},  # partial nested override
            "dimensions": ["c"],  # array override replaces entirely
            "validation": {"commands": {"python": ["pytest tests/ -x"]}},
        }
        merged = merge_config(base, override)
        assert merged["atomic"]["max_lines"] == 50
        assert merged["atomic"]["max_adjacent_methods"] == 3
        assert merged["dimensions"] == ["c"]
        # command_whitelist preserved from base; commands replaced by override
        assert merged["validation"]["command_whitelist"] == ["pytest"]
        assert merged["validation"]["commands"] == {"python": ["pytest tests/ -x"]}

    def test_returns_a_shallow_copy_of_base_when_override_is_none(self):
        base = {"a": 1, "nested": {"b": 2}}
        assert merge_config(base, None) == base
        # Not the same object: mutating the result does not touch base.
        result = merge_config(base, None)
        result["nested"]["b"] = 99
        assert base["nested"]["b"] == 2

    def test_none_values_in_override_are_skipped(self):
        base = {"goal": "g"}
        merged = merge_config(base, {"goal": None})
        assert merged["goal"] == "g"


class TestLoadConfig:
    def test_returns_none_for_a_directory_without_a_config_file(self, tmp_path):
        assert load_config(make_temp_dir(tmp_path)) is None

    def test_returns_none_for_invalid_yaml(self, tmp_path):
        d = make_temp_dir(tmp_path)
        write_config(d, "goal: [unclosed")
        assert load_config(d) is None

    def test_returns_none_when_yaml_is_not_a_mapping(self, tmp_path):
        d = make_temp_dir(tmp_path)
        write_config(d, "- just\n- a\n- list\n")
        assert load_config(d) is None

    def test_parses_a_valid_yaml_config(self, tmp_path):
        d = make_temp_dir(tmp_path)
        write_config(d, 'goal: "Test goal"\ndimensions:\n  - correctness\n')
        c = load_config(d)
        assert c is not None
        assert c["goal"] == "Test goal"
        assert c["dimensions"] == ["correctness"]


class TestLoadEffectiveConfig:
    def test_returns_defaults_when_no_project_config_exists(self, tmp_path):
        d = make_temp_dir(tmp_path)
        d.mkdir()
        effective = load_effective_config(d)
        assert effective.source == "defaults"
        assert effective.override is None
        assert effective.config.validation.commands == {}
        assert len(effective.config.dimensions) > 0

    def test_merges_partial_overrides_on_top_of_defaults(self, tmp_path):
        d = make_temp_dir(tmp_path)
        write_config(
            d,
            'goal: "Project goal"\n'
            "dimensions:\n  - correctness\n"
            "validation:\n  commands:\n    python:\n      - 'pytest tests/ -x -q'\n",
        )
        effective = load_effective_config(d)
        assert effective.source == "override"
        assert effective.override is not None
        # Overridden fields win.
        assert effective.config.goal == "Project goal"
        assert effective.config.dimensions == ["correctness"]
        assert effective.config.validation.commands == {"python": ["pytest tests/ -x -q"]}
        # Unmentioned fields fall back to defaults.
        assert effective.config.max_rounds == default_config().max_rounds
        assert effective.config.atomic.max_lines == 20
        assert effective.config.git.target_branch == "main"

    def test_missing_directory_falls_back_to_defaults(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist"
        effective = load_effective_config(nonexistent)
        assert effective.source == "defaults"
        assert effective.override is None

    def test_nested_partial_git_override_keeps_other_git_defaults(self, tmp_path):
        d = make_temp_dir(tmp_path)
        write_config(d, "git:\n  target_branch: develop\n")
        effective = load_effective_config(d)
        assert effective.config.git.target_branch == "develop"
        assert effective.config.git.auto_merge is False
        assert effective.config.git.push_per_round is False


class TestIsCommandAllowed:
    def test_requires_an_exact_match_after_trim(self):
        allowed = ["pytest tests/ -x -q", "npm run compile"]
        assert is_command_allowed("pytest tests/ -x -q", allowed)
        assert is_command_allowed("  pytest tests/ -x -q  ", allowed)  # trims whitespace
        assert not is_command_allowed("pytest", allowed)  # prefix is NOT enough
        assert not is_command_allowed(
            "pytest tests/ -x -q --extra", allowed
        )  # suffix not allowed
        assert not is_command_allowed(
            'python3 -c "import os; os.system(\'rm -rf /\')"', allowed
        )
        assert not is_command_allowed("", allowed)

    def test_returns_false_for_an_empty_command_list(self):
        assert not is_command_allowed("pytest", [])

    def test_returns_false_for_non_string_command(self):
        assert not is_command_allowed(123, ["123"])  # type: ignore[arg-type]


class TestFlattenCommands:
    def test_concatenates_all_module_command_arrays(self):
        commands = {
            "python": ["pytest tests/ -x -q", "ruff check src/"],
            "typescript": ["npm run compile"],
        }
        assert flatten_commands(commands) == [
            "pytest tests/ -x -q",
            "ruff check src/",
            "npm run compile",
        ]

    def test_handles_none_empty_and_malformed_input_safely(self):
        assert flatten_commands(None) == []
        assert flatten_commands({}) == []
        # Malformed config (a value that is not an array) is ignored, not a crash.
        assert flatten_commands({"python": "not-an-array"}) == []  # type: ignore[dict-item]


class TestValidateConfig:
    def test_reports_missing_root_when_config_is_none(self):
        assert validate_config(None) == ["root"]

    def test_reports_missing_required_fields(self):
        errors = validate_config({})
        assert "goal" in errors
        assert "dimensions" in errors
        assert "validation" in errors

    def test_reports_nested_validation_field_paths(self):
        errors = validate_config(
            {
                "goal": "g",
                "dimensions": ["correctness"],
                "validation": {"command_whitelist": "nope"},
            }
        )
        assert errors == ["validation.command_whitelist", "validation.commands"]

    def test_passes_a_complete_config(self):
        from openharness.iterate.config_loader import _default_config_dict

        assert validate_config(_default_config_dict()) == []


def test_config_filename_constant_matches_skill_convention():
    from openharness.iterate.config_loader import CONFIG_FILENAME

    assert CONFIG_FILENAME == "iterate.config.yaml"
