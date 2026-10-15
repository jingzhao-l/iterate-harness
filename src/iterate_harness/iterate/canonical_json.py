"""The canonical JSON form every half of the kernel contract digests over.

``tests/kernel_fixtures/invariant-report.ok-01.json`` states the form in rule (7), so a
second implementation can reproduce it without reading the TypeScript: keys sorted in
UTF-16 code-unit order, no insignificant whitespace, arrays kept in the caller's order,
strings exactly as JSON escapes them, and integral numbers printed without a fraction.
A digest is only a cross-language link if the bytes under it are agreed byte for byte,
so this module is the one place those rules live for the ``iterate`` producers.

WHY ``run_plan.py`` STILL HAS ITS OWN COPY. ``iterate/run_plan.py::_canonical``
implements the same rules today and is pinned by a published digest corpus
(``tests/kernel_fixtures/run-plan.ok-01.json``). Moving it here is a separate, testable
step, not part of adding a producer: switch ``_canonical`` onto ``canonical_json``,
re-run the run-plan fixture, and only delete the copy when the digests still match.
The two are also deliberately different in one respect — run_plan coerces non-string
mapping keys with ``str(k)``, which this module refuses instead. A numeric key ``1`` and
a string key ``"1"`` cannot both survive a sorted-key serialisation, and the two
implementations would resolve that collision differently, so the shared form declines
to guess. This module is additive: nothing that digests today changes shape.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

#: Non-BMP boundary. Python orders strings by code point, JavaScript by UTF-16 code
#: unit; the two orders agree across the whole BMP and diverge above it.
BMP_LIMIT = 0xFFFF

#: A key outside the BMP is ordered differently by the two implementations.
CANONICAL_JSON_E_KEY_ORDER = "CANONICAL_JSON_E_KEY_ORDER"

#: A mapping key that is not a string: no ordering rule can be agreed for it.
CANONICAL_JSON_E_KEY_TYPE = "CANONICAL_JSON_E_KEY_TYPE"

#: A value of a type the canonical form has no representation for.
CANONICAL_JSON_E_VALUE = "CANONICAL_JSON_E_VALUE"


class CanonicalJSONError(ValueError):
    """Something cannot be put into the shared form, with a code and a remedy.

    ``code``/``message``/``remedy`` follow the kernel's error frames: whoever catches
    this must be able to act on it without opening this file.
    """

    def __init__(self, code: str, message: str, remedy: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.remedy = remedy


def canonical_json(value: object) -> str:
    """Serialise ``value`` into the form both implementations agree on.

    Refuses rather than approximates: a value type with no agreed representation
    (floats, sets, arbitrary objects) or a key whose order the two runtimes disagree on
    raises, because a silently-diverging digest is worse than no digest at all — it makes
    two harnesses cite the same event when they do not.
    """
    if value is None or isinstance(value, bool):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        # Integral values print without a fraction, which is also JavaScript's
        # shortest-roundtrip form for them. Floats are refused below: JSON.stringify
        # drops a trailing ``.0`` that Python's repr keeps, so the two would not agree.
        return str(value)
    if isinstance(value, (list, tuple)):
        items: list[object] = list(value)
        return "[" + ",".join(canonical_json(item) for item in items) + "]"
    if isinstance(value, Mapping):
        return _canonical_mapping(value)
    raise CanonicalJSONError(
        CANONICAL_JSON_E_VALUE,
        f"cannot canonicalise a {type(value).__name__}",
        "keep canonicalised values to objects with string keys, arrays, strings, "
        "booleans, null and integers; a float or a set has no form both runtimes print alike",
    )


def _canonical_mapping(value: Mapping[object, object]) -> str:
    """Emit one object: keys checked, then sorted, then each value recursed into."""
    keys: list[str] = []
    for key in value:
        if not isinstance(key, str):
            raise CanonicalJSONError(
                CANONICAL_JSON_E_KEY_TYPE,
                f"a canonicalised key must be a string, got {type(key).__name__}: {key!r}",
                "use string keys; a numeric or tuple key cannot be ordered identically "
                "by both implementations once it is converted",
            )
        if any(ord(character) > BMP_LIMIT for character in key):
            raise CanonicalJSONError(
                CANONICAL_JSON_E_KEY_ORDER,
                f"key {key!r} contains a character above U+{BMP_LIMIT:X}; the two "
                "implementations order it differently",
                "keep canonicalised keys inside the BMP, where UTF-16 code-unit order "
                "and code-point order are the same",
            )
        keys.append(key)
    keys.sort()
    return (
        "{"
        + ",".join(
            f"{json.dumps(key, ensure_ascii=False)}:{canonical_json(value[key])}" for key in keys
        )
        + "}"
    )
