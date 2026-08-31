"""Local rules file management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_RULES_DIR = Path("~/.iterate-harness/local_rules").expanduser()
_RULES_FILE = _RULES_DIR / "rules.md"
_FACTS_FILE = _RULES_DIR / "facts.json"


def _ensure_dir() -> None:
    _RULES_DIR.mkdir(parents=True, exist_ok=True)


def load_local_rules() -> str:
    """Load the local rules markdown, or empty string if none exist."""
    if _RULES_FILE.exists():
        return _RULES_FILE.read_text(encoding="utf-8").strip()
    return ""


def save_local_rules(content: str) -> Path:
    """Write local rules markdown."""
    _ensure_dir()
    _RULES_FILE.write_text(content.strip() + "\n", encoding="utf-8")
    return _RULES_FILE


def load_facts() -> dict[str, object]:
    """Load extracted facts as a dict."""
    if _FACTS_FILE.exists():
        data = json.loads(_FACTS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    return {"facts": [], "last_updated": None}


def _fact_confidence(fact: dict[str, object]) -> float:
    """Extract a numeric confidence value from a fact, defaulting to 0."""
    value = fact.get("confidence")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def save_facts(facts: dict[str, object]) -> None:
    """Persist extracted facts."""
    _ensure_dir()
    facts["last_updated"] = datetime.now(timezone.utc).isoformat()
    _FACTS_FILE.write_text(
        json.dumps(facts, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def merge_facts(existing: dict[str, object], new_facts: list[dict[str, object]]) -> dict[str, object]:
    """Merge new facts into existing, deduplicating by key."""
    by_key: dict[str, dict[str, object]] = {}
    existing_facts = existing.get("facts", [])
    if isinstance(existing_facts, list):
        for existing_fact in existing_facts:
            if isinstance(existing_fact, dict):
                key = existing_fact.get("key")
                if isinstance(key, str):
                    by_key[key] = existing_fact
    for fact in new_facts:
        key = fact.get("key")
        if not isinstance(key, str) or not key:
            continue
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = fact
        else:
            new_confidence = _fact_confidence(fact)
            old_confidence = _fact_confidence(previous)
            if new_confidence >= old_confidence:
                by_key[key] = fact
    return {"facts": list(by_key.values())}
