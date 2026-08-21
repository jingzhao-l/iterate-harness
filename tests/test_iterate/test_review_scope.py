"""Tests for iterate_harness.iterate.review_scope (inventory + coverage)."""

from __future__ import annotations

from pathlib import Path

from iterate_harness.iterate.review_scope import (
    DEFAULT_SCOPE_CHUNK_SIZE,
    chunk_files,
    collect_scope_files,
    compute_coverage,
)


class TestChunkFiles:
    def test_empty_input_yields_no_chunks(self):
        assert chunk_files([]) == []

    def test_single_batch_under_chunk_size(self):
        files = [f"src/a{i}.py" for i in range(5)]
        chunks = chunk_files(files, per_chunk=10)
        assert len(chunks) == 1
        assert chunks[0] == sorted(files)

    def test_splits_at_exact_chunk_size(self):
        files = [f"f{i}.py" for i in range(6)]
        chunks = chunk_files(files, per_chunk=3)
        assert chunks == [["f0.py", "f1.py", "f2.py"], ["f3.py", "f4.py", "f5.py"]]

    def test_keeps_directory_runs_together(self):
        # 4 files but per_chunk=2: directory change must force a boundary even
        # before reaching the chunk size, so both chunks are directory-scoped.
        files = ["src/x.py", "src/y.py", "tests/x_test.py", "tests/y_test.py"]
        chunks = chunk_files(files, per_chunk=2)
        assert chunks == [
            ["src/x.py", "src/y.py"],
            ["tests/x_test.py", "tests/y_test.py"],
        ]

    def test_last_partial_chunk_is_returned(self):
        chunks = chunk_files([f"f{i}.py" for i in range(5)], per_chunk=3)
        assert len(chunks) == 2
        assert chunks[1] == ["f3.py", "f4.py"]

    def test_default_chunk_size_used_when_omitted(self):
        files = [f"f{i}.py" for i in range(DEFAULT_SCOPE_CHUNK_SIZE + 1)]
        chunks = chunk_files(files)
        assert len(chunks) == 2

    def test_non_positive_per_chunk_falls_back_to_default(self):
        files = [f"f{i}.py" for i in range(DEFAULT_SCOPE_CHUNK_SIZE + 1)]
        for bad in (0, -1, None):
            chunks = chunk_files(files, per_chunk=bad)
            assert len(chunks) == 2


class TestComputeCoverage:
    def test_empty_assigned_is_fully_covered(self):
        out = compute_coverage([], None)
        assert out.ratio == 1.0
        assert out.uncovered == []

    def test_full_coverage_when_every_assigned_file_read(self):
        assigned = ["src/a.py", "src/b.py"]
        out = compute_coverage(assigned, assigned)
        assert out.ratio == 1.0
        assert out.covered == assigned
        assert out.uncovered == []

    def test_partial_coverage_lists_uncovered(self):
        assigned = ["src/a.py", "src/b.py", "src/c.py"]
        out = compute_coverage(assigned, ["src/a.py"])
        assert out.covered == ["src/a.py"]
        assert out.uncovered == ["src/b.py", "src/c.py"]
        assert out.ratio == round(1 / 3, 3)

    def test_path_matching_normalizes_slashes_and_dotsegments(self):
        assigned = ["src/sub/file.py"]
        # dot-segments and dir separators are normalized (case is preserved; a
        # case-insensitive match is a Windows-only normcase trait).
        out = compute_coverage(assigned, ["./src/./sub/../sub/file.py"])
        assert out.ratio == 1.0
        assert out.uncovered == []

    def test_none_read_files_means_nothing_covered(self):
        assigned = ["src/a.py", "src/b.py"]
        out = compute_coverage(assigned, None)
        assert out.ratio == 0.0
        assert out.uncovered == assigned

    def test_non_string_read_entries_are_ignored(self):
        out = compute_coverage(["src/a.py"], ["src/a.py", None, 42])
        assert out.ratio == 1.0

    def test_to_dict_met_flag(self):
        out = compute_coverage(["src/a.py"], ["src/a.py"])
        d = out.to_dict()
        assert d["ratio"] == 1.0
        assert d["met"] is True


class TestCollectScopeFiles:
    def _tree(self, tmp: Path) -> None:
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("x")
        (tmp / "src" / "b.ts").write_text("x")
        (tmp / "dist").mkdir()
        (tmp / "dist" / "bundle.js").write_text("x")
        (tmp / "node_modules" / "dep").mkdir(parents=True)
        (tmp / "node_modules" / "dep" / "index.js").write_text("x")
        (tmp / "README.md").write_text("x")
        (tmp / "src" / "nested").mkdir()
        (tmp / "src" / "nested" / "c.go").write_text("x")

    def test_full_walk_includes_source_and_excludes_ignored(self, tmp_path: Path):
        self._tree(tmp_path)
        files = collect_scope_files(tmp_path, scope="full")
        assert files == ["src/a.py", "src/b.ts", "src/nested/c.go"]

    def test_changed_only_normalizes_and_sorts(self, tmp_path: Path):
        files = collect_scope_files(
            tmp_path,
            scope="changed-only",
            changed_files=["src/z.py", "src/a.ts", "NOPE.md", "../escape.py", ""],
        )
        assert files == ["src/a.ts", "src/z.py"]

    def test_changed_only_empty_when_no_files(self, tmp_path: Path):
        assert collect_scope_files(tmp_path, scope="changed-only", changed_files=None) == []