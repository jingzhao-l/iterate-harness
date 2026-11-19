"""Service exports.

``compact`` pulls in the hook executor, which imports the provider SDK client;
that chain made ``import iterate_harness.services`` cost tens of seconds even
for a caller that only wanted ``cron_scheduler`` (whose daemon start-up poll
then timed out). The public names are therefore re-exported lazily (PEP 562);
``from iterate_harness.services import compact_messages`` keeps working, but a
submodule import no longer drags the whole package in.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "compact_messages",
    "compact_conversation",
    "build_post_compact_messages",
    "estimate_conversation_tokens",
    "estimate_message_tokens",
    "estimate_tokens",
    "export_session_markdown",
    "get_project_session_dir",
    "load_session_snapshot",
    "save_session_snapshot",
    "summarize_messages",
]

#: Public name -> submodule that defines it.
_EXPORTS: dict[str, str] = {
    "build_post_compact_messages": "compact",
    "compact_conversation": "compact",
    "compact_messages": "compact",
    "estimate_conversation_tokens": "compact",
    "summarize_messages": "compact",
    "export_session_markdown": "session_storage",
    "get_project_session_dir": "session_storage",
    "load_session_snapshot": "session_storage",
    "save_session_snapshot": "session_storage",
    "estimate_message_tokens": "token_estimation",
    "estimate_tokens": "token_estimation",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
