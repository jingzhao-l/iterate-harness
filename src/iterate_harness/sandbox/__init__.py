"""IterateHarness sandbox integration helpers."""

from iterate_harness.sandbox.adapter import (
    SandboxAvailability,
    SandboxUnavailableError,
    build_sandbox_runtime_config,
    clear_stale_runtime_settings,
    get_sandbox_availability,
    remove_runtime_settings,
    wrap_command_for_sandbox,
)
from iterate_harness.sandbox.docker_backend import DockerSandboxSession, get_docker_availability
from iterate_harness.sandbox.path_validator import validate_sandbox_path
from iterate_harness.sandbox.session import (
    get_docker_sandbox,
    is_docker_sandbox_active,
    start_docker_sandbox,
    stop_docker_sandbox,
)

__all__ = [
    "DockerSandboxSession",
    "SandboxAvailability",
    "SandboxUnavailableError",
    "build_sandbox_runtime_config",
    "clear_stale_runtime_settings",
    "get_docker_availability",
    "get_docker_sandbox",
    "get_sandbox_availability",
    "is_docker_sandbox_active",
    "remove_runtime_settings",
    "start_docker_sandbox",
    "stop_docker_sandbox",
    "validate_sandbox_path",
    "wrap_command_for_sandbox",
]

