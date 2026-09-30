"""Task data models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


TaskType = Literal["local_bash", "local_agent", "remote_agent", "in_process_teammate"]
TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]

#: Environment variables whose *values* must never reach the model context, a
#: transcript, or a log line. Keys are matched case-insensitively.
SECRET_ENV_NAME_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|COOKIE|SESSION|PRIVATE)",
    re.IGNORECASE,
)
#: Placeholder substituted for a redacted secret value.
REDACTED = "***redacted***"


def redact_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Return ``env`` with every credential-looking value replaced.

    A spawned agent task's environment carries ``ANTHROPIC_API_KEY`` (plus
    proxy/CA settings). Rendering the raw record — e.g. through ``str(task)``
    in the ``task_get`` tool — would drop the key straight into the model
    context and every persisted transcript, where any prompt injection
    reached by the agent can exfiltrate it.
    """
    if env is None:
        return None
    return {
        key: (REDACTED if SECRET_ENV_NAME_PATTERN.search(key) else value)
        for key, value in env.items()
    }


@dataclass
class TaskRecord:
    """Runtime representation of a background task."""

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    cwd: str
    output_file: Path
    command: str | None = None
    prompt: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] | None = None
    argv: list[str] | None = None

    def redacted(self) -> TaskRecord:
        """Return a credential-free copy of the record."""
        clone = TaskRecord(**{**self.__dict__})
        clone.env = redact_env(self.env)
        clone.metadata = dict(self.metadata)
        return clone

    def describe(self) -> str:
        """Return a credential-free, human-readable rendering of the record."""
        lines = [
            f"id: {self.id}",
            f"type: {self.type}",
            f"status: {self.status}",
            f"description: {self.description}",
            f"cwd: {self.cwd}",
            f"output_file: {self.output_file}",
        ]
        if self.command is not None:
            lines.append(f"command: {self.command}")
        if self.argv is not None:
            lines.append(f"argv: {self.argv}")
        if self.prompt is not None:
            lines.append(f"prompt: {self.prompt}")
        if self.created_at:
            lines.append(f"created_at: {self.created_at}")
        if self.started_at is not None:
            lines.append(f"started_at: {self.started_at}")
        if self.ended_at is not None:
            lines.append(f"ended_at: {self.ended_at}")
        if self.return_code is not None:
            lines.append(f"return_code: {self.return_code}")
        if self.metadata:
            rendered = ", ".join(
                f"{key}={value}" for key, value in sorted(self.metadata.items())
            )
            lines.append(f"metadata: {rendered}")
        if self.env is not None:
            redacted = redact_env(self.env) or {}
            rendered = ", ".join(
                f"{key}={value}" for key, value in sorted(redacted.items())
            )
            lines.append(f"env: {rendered}")
        return "\n".join(lines)
