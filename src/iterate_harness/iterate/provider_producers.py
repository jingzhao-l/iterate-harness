"""What this harness offers, registered through the kernel's provider contract.

:mod:`.provider_registry` implements the contract; on its own it is a ruler -- it can
check anybody's registration and registers nobody. This module is the product half: it
puts *this* harness's two judgement producers into a registry through the same functions,
so that scenario 5 of specs 8.5/R40 ("AE 判定按 kernel provider 注册契约注册，两侧 harness
以同一契约消费") is a capability rather than a test fixture.

The two producers, and where their shape comes from:

* ``iterate-engine-ae`` -- kind ``ae-diagnosis``, method ``diagnose``, requires
  ``["evidence-pack"]``. This is the harness reading a GlassPane evidence pack and asking
  it for a verdict (:func:`.glasspane_reverification.relay_answer`).
* ``iterate-report-render`` -- kind ``report-render``, method ``render-markdown``, requires
  ``["decision-log"]``. This is the harness rendering the decision log as a Markdown report
  (``iterate_harness.iterate.pr_comment.render_markdown``).

``id``, ``kind``, ``method`` and ``requires`` below are copied verbatim from the kernel
corpus's ``registry.iterate`` block
(``tests/kernel_fixtures/provider-registration.ok-01.json``), which is the file that names
this harness's producers. ``version`` deliberately is NOT: the fixture's comment states that
its version strings are "the package/engine versions at the time of writing, not
placeholders" -- ``0.1.4`` is a claim about a particular build, and a product that restated
it would certify a claim it never measured. The version registered here is read from this
package's own single source of truth at call time (``iterate_harness.__version__``, the field
``[tool.hatch.version]`` reads), so the registration says which build is actually offering the
method.

What this module will not do:

- Re-implement a rule. Every shape and registry rule is reached by calling
  :mod:`.provider_registry` -- ``provider_from_json``, ``register_provider``,
  ``resolve_provider``, ``require_method``, ``provider_ids_in_common``. Nothing here
  re-validates a kebab token, re-prices a ``requires`` list or overwrites an id.
- Fall through. :func:`ae_method_for` and :func:`render_method_for` answer with a method
  name *only* when the material in hand carries what that method declares it needs. When
  it does not, the contract's own refusal is raised -- a provider that never declared a
  method is not a provider that declined to run one, and a caller that treated the two
  alike would report a judgement nobody registered.
- Settle a collision. :func:`ids_in_common_with` reports the ids two registries share; the
  answer is a rename or one registry, decided before the run, never "whichever map was
  consulted first".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import iterate_harness
from .provider_registry import (
    ProviderRegistration,
    ProviderRegistry,
    empty_registry,
    provider_from_json,
    provider_ids_in_common,
    register_provider,
    require_method,
    resolve_provider,
)

#: This harness's own origin, as the contract's four-value vocabulary spells it. It is the
#: field that makes rule (6) answerable: without it, "who owns this id" has no reading.
PROVIDER_ORIGIN = "iterate"

#: The AE producer: the verdict this harness reads out of an evidence pack.
AE_PROVIDER_ID = "iterate-engine-ae"
AE_PROVIDER_KIND = "ae-diagnosis"
AE_METHOD_NAME = "diagnose"

#: The report producer: the Markdown rendering of the decision log.
RENDER_PROVIDER_ID = "iterate-report-render"
RENDER_PROVIDER_KIND = "report-render"
RENDER_METHOD_NAME = "render-markdown"

#: The fields an engine evidence pack really carries, as the *engine* spells them. The
#: contract's ``requires`` tokens are these names in kebab case, and they are derived by
#: :func:`_kebab` rather than restated, so the two vocabularies cannot drift apart when one
#: list is edited. ``attribution`` and ``circuitBreaker`` are already single tokens.
EVIDENCE_PACK_FIELDS = (
    "axEvent",
    "handlerProbe",
    "stateDiff",
    "pixelDiff",
    "responsiveness",
    "crash",
    "attribution",
    "circuitBreaker",
)

#: The field names for the two whole-artefact prerequisites this harness declares.
EVIDENCE_PACK_FIELD = "evidencePack"
DECISION_LOG_FIELD = "decisionLog"

#: ``signals`` is where a pack nests its per-signal measurements.
_PACK_SIGNALS_FIELD = "signals"


def _kebab(field: str) -> str:
    """A pack field name as the contract spells it: ``circuitBreaker`` -> ``circuit-breaker``.

    The corpus's comment names the prerequisite vocabulary as "``SignalsSchema`` keys
    axEvent/handlerProbe/stateDiff/pixelDiff/responsiveness/crash rendered in kebab case", so
    the kebab token is a *rendering* of the engine's own camelCase name. Deriving it keeps one
    list to edit instead of two that can disagree.
    """
    parts = [part for part in re.split(r"(?=[A-Z])", field) if part]
    return "-".join(part.lower() for part in parts)


EVIDENCE_SIGNAL_TOKENS = frozenset(_kebab(field) for field in EVIDENCE_PACK_FIELDS)

#: The two whole-artefact tokens, derived the same way as the per-signal ones.
EVIDENCE_PACK_TOKEN = _kebab(EVIDENCE_PACK_FIELD)
DECISION_LOG_TOKEN = _kebab(DECISION_LOG_FIELD)


def product_version() -> str:
    """This product's real version, read live from its single source of truth.

    Read at call time -- ``iterate_harness.__init__.__version__``, the same field
    ``[tool.hatch.version]`` reads for the wheel and ``cli.py --version`` prints -- rather than
    imported as a value at module load. A caller that changed the package version must see it
    in what gets registered; a version frozen into this file at import time would be the same
    kind of unmeasured claim as copying the fixture's.
    """
    return str(iterate_harness.__version__)


def producer_payloads() -> list[dict[str, Any]]:
    """This harness's two registrations, in the contract's own JSON shape.

    Handed to :func:`provider_from_json` exactly as another product would hand them over the
    wire: the shape gate is the shared one, and there is no private shortcut into the
    registry.
    """
    version = product_version()
    return [
        {
            "schemaVersion": "iterate.provider/0.1",
            "id": AE_PROVIDER_ID,
            "kind": AE_PROVIDER_KIND,
            "version": version,
            "origin": PROVIDER_ORIGIN,
            "methods": [{"name": AE_METHOD_NAME, "requires": [EVIDENCE_PACK_TOKEN]}],
        },
        {
            "schemaVersion": "iterate.provider/0.1",
            "id": RENDER_PROVIDER_ID,
            "kind": RENDER_PROVIDER_KIND,
            "version": version,
            "origin": PROVIDER_ORIGIN,
            "methods": [{"name": RENDER_METHOD_NAME, "requires": [DECISION_LOG_TOKEN]}],
        },
    ]


def build_registry(payloads: Iterable[object]) -> ProviderRegistry:
    """Read each payload through the contract and register it; refuse the whole build if one fails.

    The only way this harness's registry gets built. A duplicate id -- ours against ours, or a
    smuggled second copy of a declaration -- is refused by ``register_provider`` before any map
    is handed back, so no caller can obtain a registry whose ``iterate-engine-ae`` silently
    means the second registration.
    """
    registry = empty_registry()
    for payload in payloads:
        registry = register_provider(registry, provider_from_json(payload))
    return registry


def iterate_providers() -> ProviderRegistry:
    """This harness's provider registry, built from :func:`producer_payloads`.

    Rebuilt per call rather than cached, so the registered ``version`` is always the one the
    package states right now. Two registrations through a pure gate is not a cost worth
    trading for a cached registry that could keep asserting a build which is no longer running.
    """
    return build_registry(producer_payloads())


def ids_in_common_with(payloads: Sequence[object]) -> list[str]:
    """Ids the other side's declarations and this registry both register, sorted.

    ``payloads`` is the other harness's registration list as JSON payloads; it goes through
    the same reader, so a foreign declaration this contract cannot read is a refusal rather
    than a silently dropped entry. An id on both sides is a question to settle BEFORE the run
    -- rename one of them, or register it once -- and never something the run answers by
    whichever map it consulted first: :func:`require_method` would then hand back a different
    method's prerequisites in each product while both looked satisfied. Sorted so two
    implementations can compare the list byte-for-byte.
    """
    other = build_registry(payloads)
    return provider_ids_in_common(iterate_providers(), other)


def signals_carried(material: object) -> frozenset[str]:
    """Which of the contract's prerequisite tokens ``material`` actually carries.

    A token counts as carried only when the field is present *and holds something*: an empty
    ``signals`` object, a ``null`` attribution or an empty log states nothing, and reading
    that as the signal a provider priced its method on turns "nothing was measured" into a
    verdict. Two derivations beyond the literal field names, both stated in the corpus:

    * a mapping holding any of the pack's own signals holds the pack, so it carries
      ``evidence-pack``;
    * a non-empty sequence of decision-log entries carries ``decision-log``, because that is
      what the log is -- the entries, not a wrapper.
    """
    carried: set[str] = set()
    if isinstance(material, Mapping):
        for key, value in material.items():
            token = _kebab(str(key))
            if token in EVIDENCE_SIGNAL_TOKENS and _holds_something(value):
                carried.add(token)
            elif str(key) == _PACK_SIGNALS_FIELD and isinstance(value, Mapping):
                for inner_key, inner_value in value.items():
                    inner = _kebab(str(inner_key))
                    if inner in EVIDENCE_SIGNAL_TOKENS and _holds_something(inner_value):
                        carried.add(inner)
            elif str(key) == EVIDENCE_PACK_FIELD and _holds_something(value):
                carried.add(EVIDENCE_PACK_TOKEN)
            elif str(key) == DECISION_LOG_FIELD and _holds_something(value):
                carried.add(DECISION_LOG_TOKEN)
    elif isinstance(material, Sequence) and not isinstance(material, (str, bytes)):
        if any(_is_decision_log_entry(entry) for entry in material):
            carried.add(DECISION_LOG_TOKEN)
    if carried & EVIDENCE_SIGNAL_TOKENS:
        carried.add(EVIDENCE_PACK_TOKEN)
    return frozenset(carried)


def _is_decision_log_entry(entry: object) -> bool:
    """One recorded decision: an entry object or a mapping, and it says what kind it is.

    ``type`` is the field this harness's own decision log writes per entry; an entry with no
    type recorded is a shape nobody can route, so it does not carry the log.
    """
    if isinstance(entry, Mapping):
        return _holds_something(entry.get("type"))
    return _holds_something(getattr(entry, "type", None))


def _holds_something(value: object) -> bool:
    """Whether a recorded value states something, as opposed to recording nothing."""
    if value is None:
        return False
    if isinstance(value, (str, bytes)):
        return bool(value.strip())
    if isinstance(value, (Mapping, Sequence, set, frozenset)):
        return bool(value)
    return True


def _served_method(registry: ProviderRegistry, provider_id: str, material: object) -> str:
    """The method ``provider_id`` offers for ``material``, or the contract's refusal.

    Every name asked goes through :func:`require_method`, so the declaration list the answer
    comes from is the one that was registered -- and when the material carries none of what any
    declared method requires, the refusal is reached the same way instead of a local guess. The
    name asked in that case is spelled from the material's own signals in the corpus's naming
    style (``diagnose-pixel``, ``diagnose-probed``) or from the prerequisite that is missing
    (``diagnose-without-evidence-pack``): it is deliberately a name the provider never
    declared, because the honest answer to "this pack holds nothing" is that no registered
    method covers it, not that a registered method declined.
    """
    carried = signals_carried(material)
    provider = resolve_provider(registry, provider_id)
    for declared_name in provider.method_names:
        method = require_method(registry, provider_id, declared_name)
        if set(method.requires) <= carried:
            return method.name
    return require_method(registry, provider_id, _unbacked_name(provider, carried)).name


def _unbacked_name(provider: ProviderRegistration, carried: frozenset[str]) -> str:
    base = provider.method_names[0]
    if carried:
        return f"{base}-{sorted(carried)[0]}"
    missing = next((token for token in provider.methods[0].requires), "")
    return f"{base}-without-{missing}" if missing else base


def ae_method_for(pack: Mapping[str, Any]) -> str:
    """The AE method ``iterate-engine-ae`` offers for this evidence-pack mapping.

    Answers ``"diagnose"`` only when the mapping carries the prerequisite that method
    declares. A mapping that holds no signal, no attribution, no breaker and no pack is not a
    pack this harness may read a verdict out of, so :func:`require_method` is reached and
    refuses with its code and remedy -- which the caller relays verbatim rather than
    re-decoding.
    """
    return _served_method(iterate_providers(), AE_PROVIDER_ID, pack)


def render_method_for(material: object) -> str:
    """The report method ``iterate-report-render`` offers for this rendering material.

    ``material`` is what the caller says it rendered the report from. The declared
    prerequisite is a decision log, so an empty log is a refusal: a Markdown report rendered
    from nothing would present "no findings" as a result.
    """
    return _served_method(iterate_providers(), RENDER_PROVIDER_ID, material)


__all__ = [
    "AE_METHOD_NAME",
    "AE_PROVIDER_ID",
    "AE_PROVIDER_KIND",
    "DECISION_LOG_FIELD",
    "DECISION_LOG_TOKEN",
    "EVIDENCE_PACK_FIELD",
    "EVIDENCE_PACK_FIELDS",
    "EVIDENCE_PACK_TOKEN",
    "EVIDENCE_SIGNAL_TOKENS",
    "PROVIDER_ORIGIN",
    "RENDER_METHOD_NAME",
    "RENDER_PROVIDER_ID",
    "RENDER_PROVIDER_KIND",
    "ae_method_for",
    "build_registry",
    "ids_in_common_with",
    "iterate_providers",
    "producer_payloads",
    "product_version",
    "render_method_for",
    "signals_carried",
]
