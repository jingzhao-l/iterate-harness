"""Environment self-check for iterate-harness (``ih doctor``).

The interactive REPL has had a ``/doctor`` slash command for a long time, but
only the person sitting at the terminal could run it: CI, scripts and the
WebUI console had no way to ask "is this machine ready to iterate?" and get an
actionable answer. This module is the shared engine for the top-level
``ih doctor`` command and the console's 自检 page: it runs a deterministic,
network-free set of checks, each returning a status plus a human remedy the
operator can actually execute, and never prints a credential.

Every check is defensive by construction — a broken environment must produce a
``fail`` check with a remedy, never an exception, because the whole point of
doctor is to be the one command that still works when everything else is
broken. Checks are ordered by importance (interpreter → auth → storage →
project surface), and ``run_doctor`` never performs network I/O: connectivity
problems belong to the provider call, not to a self-check that is meant to run
in an offline CI sandbox.
"""

from __future__ import annotations

import datetime
import platform as _platform
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Statuses a single check can report. ``fail`` is a hard blocker (the
#: harness cannot do its job), ``warn`` is a degraded-but-runnable condition,
#: ``ok`` is green.
OK = "ok"
WARN = "warn"
FAIL = "fail"

_ORDER = {FAIL: 0, WARN: 1, OK: 2}

#: Minimum interpreter version the package declares in ``pyproject.toml``.
MIN_PYTHON = (3, 10)


@dataclass(frozen=True)
class DoctorCheck:
    """One self-check result.

    ``id`` is a stable machine key (used by ``--json`` consumers and the
    console); ``title`` is the human label; ``detail`` carries the measured
    value or the concrete error; ``remedy`` is an executable instruction
    (never empty on ``fail``/``warn``).
    """

    id: str
    title: str
    status: str
    detail: str = ""
    remedy: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @property
    def is_ok(self) -> bool:
        return self.status == OK


@dataclass
class DoctorReport:
    """The aggregate result of :func:`run_doctor`."""

    project_root: str
    generated_at: str
    checks: list[DoctorCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no check is a hard failure (warnings are advisory)."""
        return not self.has_failures

    @property
    def has_failures(self) -> bool:
        return any(check.status == FAIL for check in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(check.status == WARN for check in self.checks)

    @property
    def summary(self) -> dict[str, int]:
        counts = {OK: 0, WARN: 0, FAIL: 0}
        for check in self.checks:
            counts[check.status] = counts.get(check.status, 0) + 1
        return counts

    def to_dict(self) -> dict[str, object]:
        return {
            "project_root": self.project_root,
            "generated_at": self.generated_at,
            "ok": self.ok,
            "has_failures": self.has_failures,
            "has_warnings": self.has_warnings,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(
    check_id: str,
    title: str,
    status: str,
    detail: str = "",
    remedy: str = "",
) -> DoctorCheck:
    return DoctorCheck(
        id=check_id,
        title=title,
        status=status,
        detail=detail,
        remedy=remedy,
    )


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _python_check() -> DoctorCheck:
    """Interpreter version floor (``requires-python = ">=3.10"``)."""
    version = sys.version_info
    current = (version.major, version.minor)
    if current >= MIN_PYTHON:
        return _check(
            "python",
            "Python 解释器",
            OK,
            detail=(
                f"{_platform.python_implementation()} "
                f"{version.major}.{version.minor}.{version.micro} at {sys.executable}"
            ),
        )
    return _check(
        "python",
        "Python 解释器",
        FAIL,
        detail=f"当前 {current[0]}.{current[1]}（需要 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}）",
        remedy="使用 Python 3.10+ 的解释器重装 iterate-harness（如 `uv sync --python 3.11`）",
    )


def _package_check() -> DoctorCheck:
    """The installed package imports and reports a version."""
    try:
        from iterate_harness import __version__ as installed

        return _check(
            "package",
            "iterate-harness 安装",
            OK,
            detail=f"版本 {installed}",
        )
    except Exception as exc:  # noqa: BLE001 - doctor must not crash
        return _check(
            "package",
            "iterate-harness 安装",
            FAIL,
            detail=str(exc),
            remedy="重新安装：`pip install -U iterate-harness`（或 `npm install -g iterate-harness`）",
        )


def _config_check() -> DoctorCheck:
    """User settings load without error (credentials never printed)."""
    try:
        from iterate_harness.config.settings import load_settings

        settings = load_settings()
        return _check(
            "config",
            "用户配置（settings.json）",
            OK,
            detail=f"活动配置已加载（模型 {settings.model}）",
        )
    except Exception as exc:  # noqa: BLE001
        return _check(
            "config",
            "用户配置（settings.json）",
            FAIL,
            detail=str(exc),
            remedy="运行 `ih setup` 重建配置，或修复 `~/.iterate-harness/settings.json`",
        )


def _auth_check() -> DoctorCheck:
    """The active provider profile has a credential configured.

    Never prints the credential itself; only the source (env / file) and the
    profile name are surfaced. A ``local`` endpoint needs no key, so it is
    always ready.
    """
    try:
        from iterate_harness.auth.manager import AuthManager
        from iterate_harness.config.settings import load_settings

        settings = load_settings()
        manager = AuthManager(settings)
        statuses = manager.get_profile_statuses()
        active = manager.get_active_profile()
        profile = statuses.get(active) or {}
        configured = bool(profile.get("configured"))
        label = str(profile.get("label") or active)
        auth_source = str(profile.get("auth_source") or "?")
        if configured:
            source = str(profile.get("auth_state") or "configured")
            return _check(
                "auth",
                "认证（活动 profile）",
                OK,
                detail=f"{label} · 来源 {source} · auth_source {auth_source}",
            )
        env_hint = _auth_env_hint(auth_source)
        return _check(
            "auth",
            "认证（活动 profile）",
            FAIL,
            detail=f"{label}（auth_source {auth_source}）未配置凭证",
            remedy=(
                f"运行 `ih auth login` 选择 {label}，"
                + (f"或设置环境变量 {env_hint}" if env_hint else "")
            ),
        )
    except Exception as exc:  # noqa: BLE001 - doctor must not crash
        return _check(
            "auth",
            "认证（活动 profile）",
            FAIL,
            detail=str(exc),
            remedy="运行 `ih auth login` 配置凭证",
        )


def _auth_env_hint(auth_source: str) -> str:
    """Map an auth source onto the env var it reads (for the remedy text)."""
    mapping = {
        "anthropic_api_key": "ANTHROPIC_API_KEY",
        "openai_api_key": "OPENAI_API_KEY",
        "deepseek_api_key": "DEEPSEEK_API_KEY",
        "dashscope_api_key": "DASHSCOPE_API_KEY",
        "moonshot_api_key": "MOONSHOT_API_KEY",
        "gemini_api_key": "GEMINI_API_KEY",
        "minimax_api_key": "MINIMAX_API_KEY",
        "zhipu_api_key": "ZHIPUAI_API_KEY",
        "siliconflow_api_key": "SILICONFLOW_API_KEY",
        "nvidia_api_key": "NVIDIA_API_KEY",
    }
    return mapping.get(auth_source, "")


def _storage_check() -> DoctorCheck:
    """The harness data directory is writable (probe, not just ``exists``)."""
    try:
        from iterate_harness.config.paths import get_data_dir

        data_dir = get_data_dir()
        _probe_writable(data_dir)
        return _check(
            "storage",
            "数据目录可写",
            OK,
            detail=str(data_dir),
        )
    except Exception as exc:  # noqa: BLE001
        return _check(
            "storage",
            "数据目录可写",
            FAIL,
            detail=str(exc),
            remedy="设置 `export ITERATE_DATA_DIR=/可写路径` 后重试",
        )


def _probe_writable(directory: Path) -> None:
    """Create+delete a probe file in *directory*, raising on failure."""
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=str(directory), delete=True):
        pass


def _project_check(project_root: Path) -> DoctorCheck:
    """The project root exists, is a directory, and (ideally) a git repo."""
    if not project_root.exists():
        return _check(
            "project",
            "项目根目录",
            FAIL,
            detail=f"不存在：{project_root}",
            remedy=f"`cd` 到真实项目目录，或 `mkdir -p {project_root}`",
        )
    if not project_root.is_dir():
        return _check(
            "project",
            "项目根目录",
            FAIL,
            detail=f"不是目录：{project_root}",
            remedy="选择一个目录作为项目根目录",
        )
    try:
        _probe_writable(project_root)
    except Exception as exc:  # noqa: BLE001
        return _check(
            "project",
            "项目根目录",
            WARN,
            detail=f"目录不可写（iterate 需要写入 .iterate）：{exc}",
            remedy="修正目录权限，或把项目移到可写路径",
        )
    from iterate_harness.iterate.git_scope import detect_repo_root

    repo_root = detect_repo_root(project_root)
    if repo_root is None:
        return _check(
            "project",
            "项目根目录",
            WARN,
            detail=f"{project_root} 不在 git 仓库内（worktree / hook / diff 依赖 git）",
            remedy="在项目内运行 `git init`（或克隆真实仓库）",
        )
    return _check(
        "project",
        "项目根目录",
        OK,
        detail=f"git 仓库根：{repo_root}",
    )


def _iterate_config_check(project_root: Path) -> DoctorCheck:
    """iterate.config.yaml presence + parseability."""
    from iterate_harness.iterate.config_loader import CONFIG_FILENAME, load_effective_config

    config_path = project_root / CONFIG_FILENAME
    if not config_path.is_file():
        return _check(
            "iterate_config",
            "iterate 项目配置",
            WARN,
            detail="未找到 iterate.config.yaml（将使用内置默认值，不配置受信校验命令）",
            remedy="运行 `ih iterate init` 生成项目配置",
        )
    try:
        effective = load_effective_config(project_root)
        return _check(
            "iterate_config",
            "iterate 项目配置",
            OK,
            detail=(
                f"{effective.source} · goal {effective.config.goal} · "
                f"dimensions {len(effective.config.dimensions)}"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return _check(
            "iterate_config",
            "iterate 项目配置",
            FAIL,
            detail=f"解析失败：{exc}",
            remedy="修复 iterate.config.yaml（或删除后运行 `ih iterate init`）",
        )


def _validation_check(project_root: Path) -> DoctorCheck:
    """Trusted validation commands configured (gate-keeping needs them)."""
    from iterate_harness.iterate.config_loader import CONFIG_FILENAME, flatten_commands, load_effective_config

    if not (project_root / CONFIG_FILENAME).is_file():
        return _check(
            "validation",
            "受信校验命令",
            WARN,
            detail="无 iterate.config.yaml（受信校验命令只能来自该文件）",
            remedy="运行 `ih iterate init` 并在 validation.commands 中登记命令",
        )
    try:
        effective = load_effective_config(project_root)
        commands = flatten_commands(effective.config.validation.commands)
    except Exception as exc:  # noqa: BLE001
        return _check(
            "validation",
            "受信校验命令",
            FAIL,
            detail=f"配置读取失败：{exc}",
            remedy="修复 iterate.config.yaml 的 validation.commands",
        )
    if not commands:
        return _check(
            "validation",
            "受信校验命令",
            WARN,
            detail="validation.commands 为空（iterate 循环无法在修复后自动校验）",
            remedy="在 iterate.config.yaml 的 validation.commands 中登记命令（如 lint / test）",
        )
    return _check(
        "validation",
        "受信校验命令",
        OK,
        detail=f"已登记 {len(commands)} 条：{', '.join(commands[:4])}{' …' if len(commands) > 4 else ''}",
    )


def _lease_check(project_root: Path) -> DoctorCheck:
    """A live foreign run lease on this project (advisory, not a blocker)."""
    try:
        from iterate_harness.iterate.run_lock import active_lease

        lease = active_lease(project_root)
    except Exception as exc:  # noqa: BLE001 - best-effort
        return _check(
            "lease",
            "运行锁",
            WARN,
            detail=f"锁探测失败：{exc}",
            remedy="手动检查 `.iterate/run.lock` 与 `ps` 确认无残留循环",
        )
    if lease is None:
        return _check("lease", "运行锁", OK, detail="无活动运行（未占用）")
    if lease.is_ours:
        return _check("lease", "运行锁", WARN, detail="当前进程持有运行锁（正常）")
    return _check(
        "lease",
        "运行锁",
        WARN,
        detail=f"检测到外部运行：{lease.describe()}（acquired {lease.acquired_at}）",
        remedy="等待其完成；如确认是残留进程，用 `ih iterate agents` 查看并停止",
    )


def _sandbox_check() -> DoctorCheck:
    """Docker sandbox availability when the user enabled it."""
    try:
        from iterate_harness.config.settings import load_settings
        from iterate_harness.sandbox.docker_backend import get_docker_availability

        settings = load_settings()
    except Exception as exc:  # noqa: BLE001
        return _check(
            "sandbox",
            "沙箱",
            WARN,
            detail=f"配置不可读：{exc}",
            remedy="修复 settings.json 后再运行 `ih doctor`",
        )
    try:
        availability = get_docker_availability(settings)
    except Exception as exc:  # noqa: BLE001
        return _check(
            "sandbox",
            "沙箱",
            WARN,
            detail=f"探测失败：{exc}",
            remedy="安装 Docker Desktop/Engine 或关闭沙箱（ITERATE_SANDBOX_ENABLED=0）",
        )
    if not availability.enabled:
        return _check("sandbox", "沙箱", OK, detail="未启用（settings.sandbox.enabled=false）")
    if availability.available:
        return _check("sandbox", "沙箱", OK, detail="Docker 可用")
    if settings.sandbox.fail_if_unavailable:
        return _check(
            "sandbox",
            "沙箱",
            FAIL,
            detail=availability.reason or "Docker 不可用",
            remedy=(
                "安装并启动 Docker，或关闭严格模式"
                "（settings.sandbox.fail_if_unavailable=false）"
            ),
        )
    return _check(
        "sandbox",
        "沙箱",
        WARN,
        detail=availability.reason or "Docker 不可用（将退化为本机执行）",
        remedy="安装并启动 Docker 以获得隔离沙箱",
    )


def _hook_check(project_root: Path) -> DoctorCheck:
    """The git pre-commit hook that gate-keeps commits on findings."""
    from iterate_harness.iterate.git_scope import detect_repo_root
    from iterate_harness.iterate.git_hook import hook_status

    repo_root = detect_repo_root(project_root)
    if repo_root is None:
        return _check(
            "hook",
            "pre-commit 门禁",
            WARN,
            detail="不在 git 仓库内（无法安装 hook）",
            remedy="在项目内运行 `git init` 后 `ih iterate hook install`",
        )
    try:
        status = hook_status(repo_root)
    except Exception as exc:  # noqa: BLE001
        return _check(
            "hook",
            "pre-commit 门禁",
            WARN,
            detail=f"状态探测失败：{exc}",
            remedy="运行 `ih iterate hook install`",
        )
    if status.get("installed"):
        return _check(
            "hook",
            "pre-commit 门禁",
            OK,
            detail=f"已安装（{status.get('path', '')}）",
        )
    return _check(
        "hook",
        "pre-commit 门禁",
        WARN,
        detail="未安装（提交不会被 finding 门禁拦截）",
        remedy="运行 `ih iterate hook install`",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

#: Every check, in run order. ``project_root`` is passed for project-scoped
#: checks; the rest are machine-global.
def run_doctor(
    project_root: str | Path | None = None,
) -> DoctorReport:
    """Run the full self-check suite for *project_root* (default: cwd)."""
    root = Path(project_root) if project_root else Path.cwd()
    root = root.expanduser().resolve()
    checks: list[DoctorCheck] = [
        _python_check(),
        _package_check(),
        _config_check(),
        _auth_check(),
        _storage_check(),
        _project_check(root),
        _iterate_config_check(root),
        _validation_check(root),
        _lease_check(root),
        _sandbox_check(),
        _hook_check(root),
    ]
    # Failures and warnings first so a broken environment is visible at the
    # top of a long terminal report instead of after a wall of green lines.
    checks.sort(key=lambda check: (_ORDER.get(check.status, 3), check.id))
    return DoctorReport(
        project_root=str(root),
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        checks=checks,
    )


def render_doctor(report: DoctorReport) -> str:
    """Render the report as aligned terminal text (mirrors the CLI)."""
    lines = [
        f"iterate-harness doctor — {report.project_root}",
        f"generated {report.generated_at}",
        "",
    ]
    status_labels = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}
    for check in report.checks:
        lines.append(f"[{status_labels.get(check.status, check.status)}] {check.title}")
        if check.detail:
            lines.append(f"      {check.detail}")
        if check.remedy:
            lines.append(f"      remedy: {check.remedy}")
    summary = report.summary
    lines.append("")
    lines.append(
        f"summary: {summary[OK]} ok, {summary[WARN]} warn, {summary[FAIL]} fail"
    )
    if report.has_failures:
        lines.append("FAIL — 存在阻塞性问题；按上方 remedy 修复后重跑 `ih doctor`。")
    elif report.has_warnings:
        lines.append("OK（有告警）— 可以运行，但建议按上方 remedy 处理告警。")
    else:
        lines.append("OK — 环境就绪。")
    return "\n".join(lines)


__all__ = [
    "FAIL",
    "OK",
    "WARN",
    "DoctorCheck",
    "DoctorReport",
    "render_doctor",
    "run_doctor",
]
