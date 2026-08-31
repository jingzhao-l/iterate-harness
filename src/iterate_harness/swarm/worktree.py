"""Git worktree isolation for swarm agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------

_VALID_SEGMENT = re.compile(r"^[a-zA-Z0-9._-]+$")
_MAX_SLUG_LENGTH = 64
_COMMON_SYMLINK_DIRS = ("node_modules", ".venv", "__pycache__", ".tox")

#: Name of the JSON metadata file stored inside each managed worktree.
_WORKTREE_META_FILENAME = ".iterate-harness-worktree.json"

#: Length of the per-repo directory hash used to namespace worktrees.
_REPO_SLUG_LENGTH = 12


def repo_namespace(repo_path: str | Path) -> str:
    """Return a deterministic, path-safe namespace for a repository.

    Worktrees are stored under ``base_dir/<repo_namespace>/<slug>`` so that
    two different repositories can never collide on the same slug (session
    workspace isolation across repos / concurrent sessions).
    """
    raw = str(Path(repo_path).resolve())
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return digest[:_REPO_SLUG_LENGTH]


def validate_worktree_slug(slug: str) -> str:
    """Sanitize and validate a worktree slug.

    Rules:
    - Max 64 characters total
    - Each '/'-separated segment must match [a-zA-Z0-9._-]+
    - '.' and '..' segments are rejected (path traversal)
    - Leading/trailing '/' are rejected

    Returns the slug unchanged if valid, raises ValueError otherwise.
    """
    if not slug:
        raise ValueError("Worktree slug must not be empty")

    if len(slug) > _MAX_SLUG_LENGTH:
        raise ValueError(
            f"Worktree slug must be {_MAX_SLUG_LENGTH} characters or fewer (got {len(slug)})"
        )

    # Reject absolute paths
    if slug.startswith("/") or slug.startswith("\\"):
        raise ValueError(f"Worktree slug must not be an absolute path: {slug!r}")

    for segment in slug.split("/"):
        if segment in (".", ".."):
            raise ValueError(
                f'Worktree slug {slug!r}: must not contain "." or ".." path segments'
            )
        if not _VALID_SEGMENT.match(segment):
            raise ValueError(
                f"Worktree slug {slug!r}: each segment must be non-empty and contain only "
                "letters, digits, dots, underscores, and dashes"
            )

    return slug


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class WorktreeInfo:
    """Metadata about a managed git worktree."""

    slug: str
    path: Path
    branch: str
    original_path: Path
    created_at: float
    agent_id: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flatten_slug(slug: str) -> str:
    """Replace '/' with '+' to avoid nested directory/branch issues."""
    return slug.replace("/", "+")


def _worktree_branch(slug: str) -> str:
    return f"worktree-{_flatten_slug(slug)}"


def _sidecar_meta_path(base_dir: Path, namespace: str, flat_slug: str) -> Path:
    """Return the sidecar metadata path for a namespaced worktree.

    Uses the same ``<ns>/<flat_slug>`` shape as ``base_dir`` so metadata
    stays outside git but uniquely keyed per worktree.
    """
    return base_dir / namespace / f"{_flatten_slug(flat_slug)}.meta.json"


def _write_metadata(
    base_dir: Path,
    worktree_path: Path,
    *,
    slug: str,
    branch: str,
    original_path: Path,
    agent_id: str | None,
) -> None:
    """Persist worktree ownership metadata as a JSON sidecar outside the worktree."""
    metadata = {
        "slug": slug,
        "branch": branch,
        "original_path": str(original_path),
        "agent_id": agent_id,
        "created_at": time.time(),
    }
    namespace = worktree_path.parent.name
    flat_slug = worktree_path.name
    meta_file = _sidecar_meta_path(base_dir, namespace, flat_slug)
    try:
        meta_file.parent.mkdir(parents=True, exist_ok=True)
        meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    except OSError:
        # Non-fatal: metadata is an extra convenience, not required for
        # worktree operation. The worktree itself still works without it.
        logger.exception(
            "[worktree] Failed to write metadata for %s", worktree_path
        )


def _read_metadata(base_dir: Path, worktree_path: Path) -> dict[str, object]:
    """Read the sidecar JSON metadata for *worktree_path*, empty dict on error."""
    namespace = worktree_path.parent.name
    flat_slug = worktree_path.name
    meta_file = _sidecar_meta_path(base_dir, namespace, flat_slug)
    if not meta_file.exists():
        return {}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        logger.exception("[worktree] Failed to read metadata at %s", meta_file)
        return {}


def _drop_sidecar(meta_path: Path) -> None:
    """Remove a sidecar metadata file, ignoring errors."""
    try:
        meta_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("[worktree] Failed to remove metadata at %s", meta_path)


async def _run_git(*args: str, cwd: Path) -> tuple[int, str, str]:
    """Run a git command, returning (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": ""},
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace").strip(),
        stderr_bytes.decode(errors="replace").strip(),
    )


async def _symlink_common_dirs(repo_path: Path, worktree_path: Path) -> None:
    """Symlink large common directories from the main repo to avoid duplication."""
    for dir_name in _COMMON_SYMLINK_DIRS:
        src = repo_path / dir_name
        dst = worktree_path / dir_name
        if dst.exists() or dst.is_symlink():
            continue
        if not src.exists():
            continue
        try:
            dst.symlink_to(src)
        except OSError as exc:
            logger.debug("Could not symlink %s -> %s: %s", dst, src, exc)  # Non-fatal


async def _remove_symlinks(worktree_path: Path) -> None:
    """Remove symlinks created by _symlink_common_dirs."""
    for dir_name in _COMMON_SYMLINK_DIRS:
        dst = worktree_path / dir_name
        if dst.is_symlink():
            try:
                dst.unlink()
            except OSError as exc:
                logger.debug("Could not remove symlink %s: %s", dst, exc)


# ---------------------------------------------------------------------------
# WorktreeManager
# ---------------------------------------------------------------------------

class WorktreeManager:
    """Manage git worktrees for isolated agent execution.

    Worktrees are stored under ``base_dir/<slug>/`` (with '/' replaced by
    '+' to keep the layout flat).  A JSON metadata file tracks active
    worktrees and their associated agent IDs so stale ones can be pruned.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir: Path = base_dir or Path.home() / ".iterate-harness" / "worktrees"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_worktree(
        self,
        repo_path: Path,
        slug: str,
        branch: str | None = None,
        agent_id: str | None = None,
    ) -> WorktreeInfo:
        """Create (or resume) a git worktree for *slug*.

        Worktrees are namespaced per repository under
        ``base_dir/<repo_namespace>/<flat_slug>`` so concurrent sessions on
        different repos never collide.

        If the worktree directory already exists and is a valid git worktree,
        it is resumed without re-running ``git worktree add``.

        Args:
            repo_path: Absolute path to the main repository.
            slug: Human-readable identifier (validated via validate_worktree_slug).
            branch: Branch name to check out; defaults to a generated ``worktree-<slug>`` name.
            agent_id: Optional identifier of the agent that owns this worktree.

        Returns:
            WorktreeInfo describing the worktree.
        """
        validate_worktree_slug(slug)
        repo_path = repo_path.resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

        flat_slug = _flatten_slug(slug)
        namespace = repo_namespace(repo_path)
        worktree_path = self.base_dir / namespace / flat_slug
        worktree_branch = branch or _worktree_branch(slug)

        # Fast resume: check whether the worktree is already registered
        if worktree_path.exists():
            code, _, _ = await _run_git(
                "rev-parse", "--git-dir", cwd=worktree_path
            )
            if code == 0:
                # Refresh metadata so the worktree is attributed to the
                # (possibly new) agent that resumed it.
                _write_metadata(
                    self.base_dir,
                    worktree_path,
                    slug=slug,
                    branch=worktree_branch,
                    original_path=repo_path,
                    agent_id=agent_id,
                )
                return WorktreeInfo(
                    slug=slug,
                    path=worktree_path,
                    branch=worktree_branch,
                    original_path=repo_path,
                    created_at=worktree_path.stat().st_mtime,
                    agent_id=agent_id,
                )

        # New worktree: -B resets an orphan branch left by a prior remove
        code, _, stderr = await _run_git(
            "worktree", "add", "-B", worktree_branch, str(worktree_path), "HEAD",
            cwd=repo_path,
        )
        if code != 0:
            raise RuntimeError(f"git worktree add failed: {stderr}")

        await _symlink_common_dirs(repo_path, worktree_path)

        _write_metadata(
            self.base_dir,
            worktree_path,
            slug=slug,
            branch=worktree_branch,
            original_path=repo_path,
            agent_id=agent_id,
        )

        return WorktreeInfo(
            slug=slug,
            path=worktree_path,
            branch=worktree_branch,
            original_path=repo_path,
            created_at=time.time(),
            agent_id=agent_id,
        )

    async def remove_worktree(self, slug: str, repo_path: Path | None = None) -> bool:
        """Remove a worktree by slug.

        Cleans up symlinks first, then runs ``git worktree remove --force``.

        When ``repo_path`` is given the namespaced path is used directly;
        otherwise the worktree is located by searching every repository
        namespace (and the legacy flat layout) under ``base_dir``.

        Returns:
            True if the worktree was removed; False if it did not exist.
        """
        validate_worktree_slug(slug)
        flat_slug = _flatten_slug(slug)

        candidates: list[Path] = []
        if repo_path is not None:
            candidates.append(self.base_dir / repo_namespace(repo_path) / flat_slug)
        if not candidates or not any(c.exists() for c in candidates):
            if self.base_dir.exists():
                candidates.extend(self.base_dir.glob(f"*/{flat_slug}"))
                # Legacy flat layout: base_dir/<flat_slug>
                candidates.append(self.base_dir / flat_slug)

        worktree_path = next((c for c in candidates if c.exists()), None)
        if worktree_path is None:
            return False

        namespace = worktree_path.parent.name
        meta_file = _sidecar_meta_path(self.base_dir, namespace, worktree_path.name)

        # Remove symlinks before git removes the directory
        await _remove_symlinks(worktree_path)

        # Determine repo root from the worktree's git metadata
        code, git_common, _ = await _run_git(
            "rev-parse", "--git-common-dir", cwd=worktree_path
        )
        if code == 0 and git_common:
            # git_common points to .git inside the main repo
            repo_path_resolved = Path(git_common).resolve().parent
            if repo_path_resolved.exists():
                await _run_git(
                    "worktree", "remove", "--force", str(worktree_path),
                    cwd=repo_path_resolved,
                )
                _drop_sidecar(meta_file)
                return True

        # Fallback: try to remove via absolute path from any working directory
        # If repo_path detection failed, attempt removal with cwd=base_dir
        code, _, _ = await _run_git(
            "worktree", "remove", "--force", str(worktree_path),
            cwd=self.base_dir,
        )
        if code == 0:
            _drop_sidecar(meta_file)
        return code == 0

    async def list_worktrees(self) -> list[WorktreeInfo]:
        """Return WorktreeInfo for every known worktree under base_dir.

        Handles both the namespaced layout (``base_dir/<ns>/<slug>``) and
        the legacy flat layout (``base_dir/<slug>``).
        """
        if not self.base_dir.exists():
            return []

        candidates: list[Path] = []
        for child in self.base_dir.iterdir():
            if not child.is_dir():
                continue
            code, _, _ = await _run_git("rev-parse", "--git-dir", cwd=child)
            if code == 0:
                # Legacy flat layout: the directory itself is a worktree.
                candidates.append(child)
                continue
            # Namespaced layout: one level deeper.
            for grandchild in child.iterdir():
                if grandchild.is_dir():
                    candidates.append(grandchild)

        results: list[WorktreeInfo] = []
        for child in candidates:
            code, _, _ = await _run_git("rev-parse", "--git-dir", cwd=child)
            if code != 0:
                continue

            # Recover branch name from HEAD
            rc, branch_out, _ = await _run_git(
                "rev-parse", "--abbrev-ref", "HEAD", cwd=child
            )
            branch = branch_out if rc == 0 else "unknown"

            # Recover original repo path from git-common-dir
            rc2, common_dir, _ = await _run_git(
                "rev-parse", "--git-common-dir", cwd=child
            )
            if rc2 == 0 and common_dir:
                original_path = Path(common_dir).resolve().parent
            else:
                original_path = child

            # Slug is the directory name (flat form); restore '/' from '+'
            slug = child.name.replace("+", "/")

            # Recover ownership metadata (agent_id, created_at). Falls back to
            # git/st_mtime when no metadata file exists (legacy worktrees).
            meta = _read_metadata(self.base_dir, child)
            agent_id = meta.get("agent_id")
            created_str = meta.get("created_at")
            created_at = child.stat().st_mtime
            if isinstance(created_str, (int, float)) and not isinstance(created_str, bool):
                created_at = float(created_str)

            results.append(
                WorktreeInfo(
                    slug=slug,
                    path=child,
                    branch=branch,
                    original_path=original_path,
                    created_at=created_at,
                    agent_id=str(agent_id) if agent_id else None,
                )
            )

        return results

    async def cleanup_stale(self, active_agent_ids: set[str] | None = None) -> list[str]:
        """Remove worktrees that have no active agent.

        Args:
            active_agent_ids: Set of agent IDs still running. If None,
                *all* worktrees with an agent_id are considered stale.

        Returns:
            List of slugs that were removed.
        """
        worktrees = await self.list_worktrees()
        removed: list[str] = []
        for info in worktrees:
            if info.agent_id is None:
                continue
            if active_agent_ids is not None and info.agent_id in active_agent_ids:
                continue
            ok = await self.remove_worktree(info.slug)
            if ok:
                removed.append(info.slug)
        return removed
