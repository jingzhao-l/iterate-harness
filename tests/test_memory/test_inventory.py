"""Memory inventory / CLI / injection-preview tests (design §17.3 P14).

The engine injects project memory into every runtime prompt; these tests pin
the read surface the operator now gets (``ih memory`` + console page): the
inventory is read-only (never creates the dir), the relevance ranking is the
engine's own scorer, the injection preview is the SAME code path as the prompt
(so it cannot drift), writes refuse to overwrite without ``--force``, and path
traversal in the read route is rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iterate_harness.cli import app as cli_app
from iterate_harness.config.settings import Settings
from iterate_harness.memory import (
    add_memory_entry,
    build_memory_injection,
    build_memory_injection_preview,
    build_memory_inventory,
    list_memory_files,
    prune_memory_index,
    read_memory_entry,
    remove_memory_entry,
)
from iterate_harness.prompts.context import build_runtime_system_prompt


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    monkeypatch.setenv("ITERATE_DATA_DIR", str(data))
    return data


def _seed(project: Path) -> None:
    add_memory_entry(project, "pytest quirks", "Pytest markers and fixtures")
    add_memory_entry(project, "docker notes", "Docker compose caveats")


def test_empty_project_is_read_only_and_reports_not_exists(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    inventory = build_memory_inventory(project)
    assert inventory["exists"] is False
    assert inventory["summary"]["entries"] == 0
    assert inventory["entries"] == []
    # The directory must not have been created by looking.
    assert not inventory["memory_dir"].endswith("memory")
    assert not Path(inventory["memory_dir"]).exists()


def test_inventory_lists_entries_with_metadata(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    inventory = build_memory_inventory(project)
    assert inventory["exists"] is True
    assert inventory["summary"]["entries"] == 2
    names = {entry["name"] for entry in inventory["entries"]}
    assert names == {"pytest_quirks.md", "docker_notes.md"}
    by_name = {entry["name"]: entry for entry in inventory["entries"]}
    assert by_name["pytest_quirks.md"]["description"] == "Pytest markers and fixtures"
    assert by_name["pytest_quirks.md"]["bytes"] > 0
    assert by_name["pytest_quirks.md"]["modified_at"].endswith("+00:00")
    assert inventory["entrypoint"]["exists"] is True
    assert inventory["entrypoint"]["lines"] >= 3


def test_query_ranks_with_the_engine_scorer(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    inventory = build_memory_inventory(project, query="fix pytest fixtures")
    assert [entry["name"] for entry in inventory["matches"]] == ["pytest_quirks.md"]


def test_injection_preview_uses_the_prompt_code_path(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    settings = Settings()
    preview = build_memory_injection_preview(project, "fix pytest fixtures", settings=settings)
    assert preview["enabled"] is True
    assert "pytest_quirks.md" in preview["rendered"]
    # The preview must equal what the runtime prompt would inject: same builder,
    # same budgets. Assert the rendered section appears verbatim in the prompt.
    prompt = build_runtime_system_prompt(settings, cwd=project, latest_user_prompt="fix pytest fixtures")
    assert preview["rendered"] in prompt


def test_injection_preview_respects_disabled_memory(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    settings = Settings()
    settings.memory.enabled = False
    preview = build_memory_injection_preview(project, "anything", settings=settings)
    assert preview["enabled"] is False
    assert "disabled" in preview["reason"]


def test_inventory_reads_the_real_settings_when_none_passed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inventory must consult the on-disk settings, not assume enabled."""
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"memory": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ITERATE_CONFIG_DIR", str(config_dir))
    inventory = build_memory_inventory(project, prompt="anything")
    assert inventory["summary"]["enabled"] is False
    assert inventory["injection"] is not None
    assert inventory["injection"]["enabled"] is False


def test_injection_preview_reports_clipped_files(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    add_memory_entry(project, "big notes", "x" * 9000)
    injection = build_memory_injection(project, "big", max_entry_chars=8000)
    assert any(item.truncated for item in injection.files)
    assert injection.summary()["truncated_files"][0]["name"] == "big_notes.md"


def test_read_entry_returns_content_and_rejects_traversal(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    entry = read_memory_entry(project, "pytest_quirks.md")
    assert entry is not None
    assert entry["content"] == "Pytest markers and fixtures\n"
    assert read_memory_entry(project, "missing") is None
    # Traversal / absolute / non-markdown names are all refused.
    assert read_memory_entry(project, "../secret.md") is None
    assert read_memory_entry(project, str(tmp_path / "x.md")) is None
    assert read_memory_entry(project, "notes.txt") is None


def test_add_refuses_overwrite_unless_allowed(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    add_memory_entry(project, "dup", "first", overwrite=False)
    with pytest.raises(FileExistsError):
        add_memory_entry(project, "dup", "second", overwrite=False)
    add_memory_entry(project, "dup", "second", overwrite=True)
    entry = read_memory_entry(project, "dup.md")
    assert entry is not None
    assert entry["content"] == "second\n"


def test_remove_prunes_index_and_prune_repairs_dead_links(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    assert remove_memory_entry(project, "docker_notes") is True
    assert "docker_notes.md" not in [p.name for p in list_memory_files(project)]
    entrypoint = Path(build_memory_inventory(project)["entrypoint"]["path"])
    assert "docker_notes.md" not in entrypoint.read_text(encoding="utf-8")
    # A dead link repaired without the file present.
    entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "- [ghost](ghost.md)\n", encoding="utf-8")
    assert prune_memory_index(project, "ghost") is True
    assert "ghost.md" not in entrypoint.read_text(encoding="utf-8")


def test_inventory_reports_dead_index_links_as_problems(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    entrypoint = Path(build_memory_inventory(project)["entrypoint"]["path"])
    entrypoint.write_text(entrypoint.read_text(encoding="utf-8") + "- [ghost](ghost.md)\n", encoding="utf-8")
    inventory = build_memory_inventory(project)
    assert any(problem["name"] == "ghost.md" for problem in inventory["problems"])


# ---- CLI ----

def test_cli_list_json_round_trip(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    result = CliRunner().invoke(
        cli_app, ["memory", "list", "--project-root", str(project), "--json"]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["entries"] == 2
    assert {entry["name"] for entry in payload["entries"]} == {
        "pytest_quirks.md",
        "docker_notes.md",
    }


def test_cli_list_on_empty_project_succeeds(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    result = CliRunner().invoke(cli_app, ["memory", "list", "--project-root", str(project)])
    assert result.exit_code == 0
    assert "does not exist" in result.output


def test_cli_add_and_conflict(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    result = CliRunner().invoke(
        cli_app,
        ["memory", "add", "deploy notes", "--content", "use canary", "--project-root", str(project)],
    )
    assert result.exit_code == 0
    assert "deploy_notes.md" in result.output
    # Duplicate without --force is refused.
    result = CliRunner().invoke(
        cli_app,
        ["memory", "add", "deploy notes", "--content", "x", "--project-root", str(project)],
    )
    assert result.exit_code == 1
    assert "already exists" in result.output
    # --force overwrites.
    result = CliRunner().invoke(
        cli_app,
        ["memory", "add", "deploy notes", "--content", "use canary first", "--force", "--project-root", str(project)],
    )
    assert result.exit_code == 0


def test_cli_search_show_preview_remove(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    _seed(project)
    result = CliRunner().invoke(
        cli_app, ["memory", "search", "pytest", "--project-root", str(project)]
    )
    assert result.exit_code == 0
    assert "pytest_quirks.md" in result.output

    result = CliRunner().invoke(
        cli_app, ["memory", "show", "pytest_quirks", "--project-root", str(project)]
    )
    assert result.exit_code == 0
    assert "Pytest markers and fixtures" in result.output

    result = CliRunner().invoke(
        cli_app, ["memory", "show", "missing", "--project-root", str(project)]
    )
    assert result.exit_code == 1

    result = CliRunner().invoke(
        cli_app,
        ["memory", "preview", "fix pytest fixtures", "--project-root", str(project)],
    )
    assert result.exit_code == 0
    assert "pytest_quirks.md" in result.output

    result = CliRunner().invoke(
        cli_app, ["memory", "remove", "docker_notes", "--yes", "--project-root", str(project)]
    )
    assert result.exit_code == 0
    assert "docker_notes.md" not in [p.name for p in list_memory_files(project)]
