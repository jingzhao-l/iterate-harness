"""Tests for per-dimension resource overrides (model / concurrency / token_budget)."""

from __future__ import annotations

import pytest

from openharness.commands.iterate import iterate_command_handler
from openharness.commands.registry import CommandContext
from openharness.iterate import config_loader, review
from openharness.iterate.types import DimensionResources, IterateConfig


def make_context(cwd) -> CommandContext:
    return CommandContext(engine=None, cwd=str(cwd))  # type: ignore[arg-type]


# ---- parse_dimension_resources ------------------------------------------


class TestParseDimensionResources:
    def test_none_and_empty(self):
        assert config_loader.parse_dimension_resources(None) == ({}, [])
        assert config_loader.parse_dimension_resources({}) == ({}, [])

    def test_full_override(self):
        resources, errors = config_loader.parse_dimension_resources(
            {
                "security": {"model": "claude-opus-4", "concurrency": 2, "token_budget": 50000},
                "style-tests": {"model": "claude-haiku"},
            }
        )
        assert errors == []
        assert resources["security"] == DimensionResources(
            model="claude-opus-4", concurrency=2, token_budget=50000
        )
        assert resources["style-tests"].model == "claude-haiku"
        assert resources["style-tests"].concurrency is None

    def test_concurrency_clamped(self):
        resources, _ = config_loader.parse_dimension_resources(
            {"a": {"concurrency": 99}, "b": {"concurrency": 0}}
        )
        assert resources["a"].concurrency == config_loader.MAX_DIMENSION_CONCURRENCY
        assert resources["b"].concurrency == config_loader.MIN_DIMENSION_CONCURRENCY

    def test_invalid_types_reported_and_skipped(self):
        resources, errors = config_loader.parse_dimension_resources(
            {
                "a": {"model": 123, "concurrency": "fast", "token_budget": -5},
                "b": "not-a-mapping",
            }
        )
        assert resources["a"] == DimensionResources()
        assert "b" not in resources
        assert len(errors) == 4

    def test_non_mapping_root(self):
        resources, errors = config_loader.parse_dimension_resources(["nope"])
        assert resources == {}
        assert errors == ["dimension_resources must be a mapping"]


# ---- config plumbing -----------------------------------------------------


class TestConfigPlumbing:
    def test_load_effective_config_reads_dimension_resources(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test project
dimensions: [security, style-tests]
dimension_resources:
  security:
    model: claude-opus-4
    concurrency: 2
    token_budget: 80000
  style-tests:
    model: claude-haiku
""",
            encoding="utf-8",
        )
        effective = config_loader.load_effective_config(tmp_path)
        assert effective.config.dimension_resources["security"].model == "claude-opus-4"
        assert effective.config.dimension_resources["security"].token_budget == 80000
        assert effective.config.dimension_resources["style-tests"].concurrency is None

    def test_validate_config_reports_resource_errors(self):
        errors = config_loader.validate_config(
            {
                "goal": "g",
                "dimensions": ["security"],
                "validation": {"command_whitelist": [], "commands": {}},
                "dimension_resources": {"security": {"concurrency": "many"}},
            }
        )
        assert "dimension_resources.security.concurrency must be an integer" in errors

    def test_validate_config_accepts_valid_resources(self):
        errors = config_loader.validate_config(
            {
                "goal": "g",
                "dimensions": ["security"],
                "validation": {"command_whitelist": [], "commands": {}},
                "dimension_resources": {"security": {"concurrency": 4}},
            }
        )
        assert errors == []


# ---- plan integration ----------------------------------------------------


class TestPlanIntegration:
    def _config(self) -> IterateConfig:
        cfg = IterateConfig()
        cfg.dimensions = ["security", "style-tests"]
        cfg.dimension_resources = {
            "security": DimensionResources(model="strong-model", concurrency=2, token_budget=50000),
        }
        return cfg

    def test_plan_carries_resources_and_prompt_clause(self):
        plan = review.build_review_plan(
            config=self._config(), mode="dry-run", max_review_rounds=3
        )
        security = next(d for d in plan.dimensions if d.id == "security")
        style = next(d for d in plan.dimensions if d.id == "style-tests")
        assert security.resources == DimensionResources(
            model="strong-model", concurrency=2, token_budget=50000
        )
        assert "model=strong-model" in security.reviewer_prompt
        assert "max concurrent reviewer agents=2" in security.reviewer_prompt
        assert "token budget=50000" in security.reviewer_prompt
        assert style.resources is None or style.resources.is_empty()
        assert "Resource plan" not in style.reviewer_prompt

    def test_plan_to_dict_serializes_resources(self):
        plan = review.build_review_plan(
            config=self._config(), mode="dry-run", max_review_rounds=3
        )
        data = review.plan_to_dict(plan)
        security = next(d for d in data["dimensions"] if d["id"] == "security")
        style = next(d for d in data["dimensions"] if d["id"] == "style-tests")
        assert security["resources"] == {
            "model": "strong-model",
            "concurrency": 2,
            "token_budget": 50000,
        }
        assert "resources" not in style

    def test_default_config_has_no_resources(self):
        plan = review.build_review_plan(config=IterateConfig(), mode="dry-run", max_review_rounds=3)
        assert all(d.resources is None for d in plan.dimensions)
        data = review.plan_to_dict(plan)
        assert all("resources" not in d for d in data["dimensions"])


# ---- /iterate config display ---------------------------------------------


class TestConfigDisplay:
    @pytest.mark.asyncio
    async def test_config_shows_dimension_resources(self, tmp_path):
        (tmp_path / "iterate.config.yaml").write_text(
            """
goal: test project
dimension_resources:
  security:
    model: claude-opus-4
""",
            encoding="utf-8",
        )
        result = await iterate_command_handler("config", make_context(tmp_path))
        assert "dimension resources [security]" in (result.message or "")
        assert "claude-opus-4" in (result.message or "")
