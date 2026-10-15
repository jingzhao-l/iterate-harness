"""Load hooks from settings."""

from __future__ import annotations

import logging
from collections import defaultdict

from iterate_harness.config.settings import Settings
from iterate_harness.hooks.events import HookEvent
from iterate_harness.hooks.schemas import HookDefinition
from iterate_harness.plugins.types import LoadedPlugin

log = logging.getLogger(__name__)


class HookRegistry:
    """Store hooks grouped by event."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookDefinition]] = defaultdict(list)

    def register(self, event: HookEvent, hook: HookDefinition) -> None:
        """Register one hook."""
        self._hooks[event].append(hook)

    def get(self, event: HookEvent) -> list[HookDefinition]:
        """Return hooks registered for an event."""
        return list(self._hooks.get(event, []))

    def summary(self) -> str:
        """Return a human-readable hook summary."""
        lines: list[str] = []
        for event in HookEvent:
            hooks = self.get(event)
            if not hooks:
                continue
            lines.append(f"{event.value}:")
            for hook in hooks:
                matcher = getattr(hook, "matcher", None)
                detail = getattr(hook, "command", None) or getattr(hook, "prompt", None) or getattr(hook, "url", None) or ""
                suffix = f" matcher={matcher}" if matcher else ""
                lines.append(f"  - {hook.type}{suffix}: {detail}")
        return "\n".join(lines)


def load_hook_registry(settings: Settings, plugins: list[LoadedPlugin] | None = None) -> HookRegistry:
    """Load hooks from the current settings object."""
    registry = HookRegistry()
    valid_events = [event.value for event in HookEvent]
    for raw_event, hooks in settings.hooks.items():
        try:
            event = HookEvent(raw_event)
        except ValueError:
            # A typo'd event name would otherwise silently drop the whole
            # section: the hook never fires and nothing tells the user. Log
            # the invalid value together with the accepted spellings.
            log.warning(
                "Ignoring hooks with unknown event %r (valid events: %s)",
                raw_event,
                ", ".join(valid_events),
            )
            continue
        for hook in hooks:
            registry.register(event, hook)
    for plugin in plugins or []:
        if not plugin.enabled:
            continue
        for raw_event, plugin_event_hooks in plugin.hooks.items():
            try:
                event = HookEvent(raw_event)
            except ValueError:
                log.warning(
                    "Ignoring plugin %r hooks with unknown event %r (valid events: %s)",
                    getattr(plugin, "name", plugin),
                    raw_event,
                    ", ".join(valid_events),
                )
                continue
            for plugin_hook in plugin_event_hooks:
                if isinstance(plugin_hook, HookDefinition):
                    registry.register(event, plugin_hook)
    return registry
