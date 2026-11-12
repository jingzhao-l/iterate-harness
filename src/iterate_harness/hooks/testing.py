"""Test registered lifecycle hooks without starting a full run (``ih hooks test``).

The engine only runs a hook when the real lifecycle event fires — a
``pre_tool_use`` hook first runs when the model calls a tool, perhaps minutes
into an expensive run. Until now an operator had no way to answer "will this
hook fire, and will it pass?" before committing to a run; the execution journal
only showed hooks *after* they had already failed inside one.

:func:`run_hook_test` builds exactly the registry the engine builds (settings +
enabled plugins), selects the hooks registered for one event, applies their
matchers to a synthetic payload, and executes them through the same
:class:`~iterate_harness.hooks.executor.HookExecutor` the engine uses. Every
execution lands in the same journal, so a test is visible in
``ih hooks log`` / the console. Command and HTTP hooks run for real; prompt /
agent hooks require a model call and are reported as ``skipped`` unless
``with_model`` is set (then the configured provider is used).

Dry-run mode (``dry_run=True``) never executes anything: it reports which hooks
*would* run for the event, which is what a CI job wants to assert.

Exit-code policy lives in the CLI: any executed hook that fails or blocks makes
the command exit 1, so a broken ``block_on_failure`` gate is caught before it
can fail a real run.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iterate_harness.api.client import ApiMessageRequest, ApiStreamEvent, SupportsStreamingMessages
from iterate_harness.config.settings import load_settings
from iterate_harness.plugins import load_plugins

from .events import HookEvent
from .executor import HookExecutionContext, HookExecutor, _matches_hook
from .inventory import _apply_validation, _hook_row
from .loader import HookRegistry, load_hook_registry
from .schemas import HookDefinition

UTC = timezone.utc

#: Hook kinds that can be exercised without a model provider.
LIVE_TYPES = frozenset({"command", "http"})

#: Hook kinds that need an API client.
MODEL_TYPES = frozenset({"prompt", "agent"})


class _UnavailableApiClient:
    """Stand-in client so command/http hooks can run without credentials.

    Prompt/agent hooks are never handed to this client: :func:`run_hook_test`
    marks them ``skipped`` before execution. If a future change ever routes one
    here, the failure is explicit instead of silently using a real provider.
    """

    async def _fail(self) -> AsyncIterator[ApiStreamEvent]:  # pragma: no cover - defensive only
        raise RuntimeError("prompt/agent hooks require `ih hooks test --with-model`")
        yield  # make this an async generator

    def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        """Return an iterator that fails loudly if a model hook reaches it."""
        return self._fail()


def build_test_payload(
    event: HookEvent,
    *,
    tool_name: str = "",
    prompt: str = "",
    notification_type: str = "",
    message: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the synthetic payload handed to hooks (and exposed in the result).

    ``event`` is always the selected event; only the fields the caller supplied
    are included, mirroring the real payloads callers build. ``test: true`` is
    included so a hook can tell a dry run from a real lifecycle event (it must
    be able to branch on that without guessing from a missing field).
    """
    payload: dict[str, Any] = {"event": event.value, "test": True}
    if tool_name:
        payload["tool_name"] = tool_name
    if prompt:
        payload["prompt"] = prompt
    if notification_type:
        payload["notification_type"] = notification_type
    if message:
        payload["message"] = message
    for key, value in (extra or {}).items():
        if key == "event":
            # The selected event is authoritative: a payload that claims a
            # different event would make matchers disagree with the registry.
            continue
        payload[str(key)] = value
    return payload


def _source_map(settings: Any, plugins: list[Any]) -> dict[int, str]:
    """Map each hook definition object to its origin (settings/plugin name)."""
    sources: dict[int, str] = {}
    for hooks in (getattr(settings, "hooks", {}) or {}).values():
        for hook in hooks or []:
            sources[id(hook)] = "settings"
    for plugin in plugins:
        if not getattr(plugin, "enabled", True):
            continue
        for hooks in (getattr(plugin, "hooks", {}) or {}).values():
            for hook in hooks or []:
                sources[id(hook)] = f"plugin:{getattr(plugin, 'name', 'unknown')}"
    return sources


def _model_client(settings: Any) -> tuple[SupportsStreamingMessages | None, str]:
    """Resolve the configured API client for prompt/agent hooks (best-effort)."""
    try:
        from iterate_harness.ui.runtime import _resolve_api_client_from_settings

        return _resolve_api_client_from_settings(settings), ""
    except SystemExit:
        return None, "no model credentials configured (run `ih auth login` or set an API key)"
    except Exception as exc:  # noqa: BLE001 - a missing credential is a skip, not a crash
        return None, f"could not build a model client: {type(exc).__name__}: {exc}"


async def _execute_one(
    hook: HookDefinition,
    event: HookEvent,
    payload: dict[str, Any],
    context: HookExecutionContext,
) -> Any:
    """Execute one hook through the engine's executor (records to the journal)."""
    registry = HookRegistry()
    registry.register(event, hook)
    executor = HookExecutor(registry, context)
    aggregated = await executor.execute(event, payload)
    return aggregated.results[0] if aggregated.results else None


async def run_hook_test(
    project_root: str | Path,
    event: HookEvent,
    *,
    payload: dict[str, Any] | None = None,
    dry_run: bool = False,
    with_model: bool = False,
    settings: Any = None,
) -> dict[str, Any]:
    """Run (or dry-run) every hook registered for *event*.

    Returns a JSON-ready model shared by the CLI and its tests. Loading a broken
    settings/plugin file raises here (the caller turns it into a one-line error):
    unlike the read-only inventory, a *test* that cannot load the real registry
    would be a false negative.
    """
    root = Path(project_root).expanduser().resolve()
    resolved_settings = settings or load_settings()
    plugins = load_plugins(resolved_settings, root)
    registry = load_hook_registry(resolved_settings, plugins)
    sources = _source_map(resolved_settings, plugins)
    event_payload = payload or build_test_payload(event)

    matched: list[tuple[HookDefinition, str]] = []
    for hook in registry.get(event):
        if _matches_hook(hook, event_payload):
            matched.append((hook, sources.get(id(hook), "settings")))

    api_client: SupportsStreamingMessages | None = None
    model_error = ""
    needs_model = any(getattr(hook, "type", "") in MODEL_TYPES for hook, _ in matched)
    if needs_model and with_model and not dry_run:
        api_client, model_error = _model_client(resolved_settings)
    elif needs_model and not with_model:
        model_error = "prompt/agent hooks need a model; pass --with-model to run them"

    context = HookExecutionContext(
        cwd=root,
        api_client=api_client or _UnavailableApiClient(),
        default_model=str(getattr(resolved_settings, "model", "") or ""),
    )

    results: list[dict[str, Any]] = []
    for hook, source in matched:
        row = _hook_row(hook, source=source, event=event.value)
        # Reuse the inventory's static validation (dead command, bad URL) so a
        # test names the configuration problem before it tries to spawn it.
        problems: list[dict[str, str]] = []
        _apply_validation(row, problems=problems)
        result_row: dict[str, Any] = {
            "event": event.value,
            "type": row.get("type", ""),
            "source": source,
            "matcher": row.get("matcher", ""),
            "detail": row.get("detail", ""),
            "timeout_seconds": row.get("timeout_seconds", 30),
            "block_on_failure": row.get("block_on_failure", False),
            "issues": row.get("issues", []),
            "status": "pending",
            "success": False,
            "blocked": False,
            "reason": "",
            "output": "",
            "duration_ms": 0,
            "returncode": None,
            "status_code": None,
        }
        hook_type = str(row.get("type") or "")
        if dry_run:
            result_row["status"] = "would-run" if hook_type in LIVE_TYPES or with_model else "skipped"
            if hook_type in MODEL_TYPES and not with_model:
                result_row["reason"] = model_error
            results.append(result_row)
            continue
        if hook_type in MODEL_TYPES and api_client is None:
            result_row["status"] = "skipped"
            result_row["reason"] = model_error
            results.append(result_row)
            continue
        started = time.monotonic()
        result = await _execute_one(hook, event, event_payload, context)
        duration_ms = int((time.monotonic() - started) * 1000)
        if result is None:  # pragma: no cover - executor always returns one result
            result_row["status"] = "fail"
            result_row["reason"] = "hook produced no result"
        else:
            result_row.update(
                {
                    "status": "pass" if result.success else ("blocked" if result.blocked else "fail"),
                    "success": bool(result.success),
                    "blocked": bool(result.blocked),
                    "reason": str(result.reason or ""),
                    "output": str(result.output or ""),
                    "duration_ms": duration_ms,
                    "returncode": (result.metadata or {}).get("returncode"),
                    "status_code": (result.metadata or {}).get("status_code"),
                }
            )
        results.append(result_row)

    passed = sum(1 for row in results if row["status"] == "pass")
    failed = sum(1 for row in results if row["status"] == "fail")
    blocked = sum(1 for row in results if row["status"] == "blocked")
    skipped = sum(1 for row in results if row["status"] == "skipped")
    return {
        "project_root": str(root),
        "generated_at": datetime.now(UTC).isoformat(),
        "event": event.value,
        "dry_run": bool(dry_run),
        "with_model": bool(with_model),
        "payload": event_payload,
        "summary": {
            "matched": len(results),
            "passed": passed,
            "failed": failed,
            "blocked": blocked,
            "skipped": skipped,
            "would_run": sum(1 for row in results if row["status"] == "would-run"),
            "has_failures": failed + blocked > 0,
        },
        "model_error": model_error,
        "results": results,
    }


def render_hook_test_table(view: dict[str, Any]) -> str:
    """Render a hook test as aligned terminal text."""
    summary = view.get("summary") or {}
    mode = "dry run" if view.get("dry_run") else "executed"
    lines = [
        f"Hook test — {view.get('event', '')} ({mode})",
        (
            f"matched: {summary.get('matched', 0)} · passed: {summary.get('passed', 0)} · "
            f"failed: {summary.get('failed', 0)} · blocked: {summary.get('blocked', 0)} · "
            f"skipped: {summary.get('skipped', 0)}"
        ),
    ]
    if view.get("model_error"):
        lines.append(f"note: {view['model_error']}")
    lines.append("")
    rows = view.get("results") or []
    if not rows:
        lines.append("No hook is registered for this event (or none matched the payload).")
        return "\n".join(lines)
    header = f"{'type':<8} {'source':<22} {'matcher':<14} {'status':<9} {'ms':>6} detail"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        detail = str(row.get("detail") or "")
        if len(detail) > 50:
            detail = detail[:47] + "..."
        matcher = str(row.get("matcher") or "—")
        if len(matcher) > 14:
            matcher = matcher[:11] + "..."
        lines.append(
            f"{str(row.get('type') or ''):<8} {str(row.get('source') or ''):<22} "
            f"{matcher:<14} {str(row.get('status') or ''):<9} "
            f"{int(row.get('duration_ms') or 0):>6} {detail}"
        )
        reason = str(row.get("reason") or "").strip()
        if reason and row.get("status") not in {"pass", "would-run"}:
            for reason_line in reason.splitlines():
                lines.append(f"{'':<8} {reason_line}")
    return "\n".join(lines)


def render_hook_test_markdown(view: dict[str, Any]) -> str:
    """Render a hook test as a Markdown digest (PR / incident notes)."""
    summary = view.get("summary") or {}
    mode = "dry run" if view.get("dry_run") else "executed"
    lines = [
        f"# Hook test — `{view.get('event', '')}`",
        "",
        (
            f"Mode: **{mode}** · matched {summary.get('matched', 0)} · "
            f"passed {summary.get('passed', 0)} · failed {summary.get('failed', 0)} · "
            f"blocked {summary.get('blocked', 0)} · skipped {summary.get('skipped', 0)}"
        ),
        "",
    ]
    if view.get("model_error"):
        lines.extend([f"> {view['model_error']}", ""])
    rows = view.get("results") or []
    if not rows:
        lines.append("_No hook is registered for this event (or none matched the payload)._")
        return "\n".join(lines)
    lines.append("| type | source | matcher | status | ms | reason |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        reason = str(row.get("reason") or "").replace("|", "\\|").replace("\n", " ")
        if len(reason) > 120:
            reason = reason[:117] + "..."
        lines.append(
            f"| {row.get('type', '')} | {row.get('source', '')} | {row.get('matcher', '')} | "
            f"{row.get('status', '')} | {row.get('duration_ms', 0)} | {reason} |"
        )
    return "\n".join(lines)


__all__ = [
    "LIVE_TYPES",
    "MODEL_TYPES",
    "build_test_payload",
    "render_hook_test_markdown",
    "render_hook_test_table",
    "run_hook_test",
]
