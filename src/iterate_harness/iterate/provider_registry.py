"""What a judgement producer offers, stated once so both harnesses read the same shape.

A second, independent implementation of the provider-registration contract published as
``@iterate/kernel``'s ``kernel/src/provider-registration.ts``, coded from
``tests/kernel_fixtures/provider-registration.ok-01.json`` -- whose ``comment`` states every
rule in prose -- rather than from the TypeScript. The fixture is the only thing the two
implementations can both be checked against, so the value here is that it agrees with the
kernel because the rule holds, not because one file was copied into the other.

The rules, restated from the fixture so this file stands on its own:

1. ``schemaVersion`` is exactly ``iterate.provider/0.1``.
2. ``id``, ``kind``, ``version`` and ``methods`` are required; ``origin`` is optional. ``id``
   matches ``^[a-z0-9]+(?:-[a-z0-9]+)*$`` and is <=64 chars; ``kind`` is
   ``ae-diagnosis|report-render|evidence-source``; ``version`` is 1..64 chars; ``origin`` is
   ``kernel|glasspane|iterate|plugin``.
3. ``methods`` holds 1..32 entries, each with a required ``name`` (same kebab pattern, <=64)
   and a required ``requires`` array (0..16 entries, same kebab pattern, <=64 each). An empty
   ``requires`` is a claim the provider makes -- "this needs nothing" -- not an absence, so a
   missing one is a shape failure rather than an empty tuple.
4. A method name repeated inside one registration is refused, not collapsed: the two
   declarations can carry different prerequisites, and last-write-wins would let a provider
   offer a method under prerequisites nobody agreed to. JSON Schema cannot say this
   (``uniqueItems`` compares whole objects, and two entries differing only in ``requires``
   are not duplicates), which is why the fixture's
   ``duplicate-method-differs-only-by-requires`` case is the documented divergence: the
   published schema accepts it and a validator has to refuse it.
5. Registry semantics, which are functions rather than schema features: registering an id
   already present is refused rather than overwriting; resolution is exact -- no default
   provider, no fuzzy match, no "the only one registered"; asking a provider for a method it
   does not offer is refused with what it *does* offer named; and a registry is replaced
   whole on each registration, never mutated, so a half-applied registration cannot leave one
   map with the provider and the next without it.
6. The same id meaning two different things across the boundary is what must be settled
   before a run rather than during one. ``provider_ids_in_common`` returns exactly the ids to
   settle; the answer is a rename or one registry, never "whichever map was consulted first".

What this module will not do:

- Run or reach anything. A registration is what a producer *claims* to offer; nothing here
  calls a provider, checks that it works, or looks for the signals its ``requires`` names.
- Guess. There is no default provider, no prefix or fuzzy match, no version negotiation and
  no "only one is registered, so it must be that one". An id nobody registered is a refusal.
- Overwrite. :func:`register_provider` returns a new registry or refuses; it never replaces an
  existing id, because in a two-harness ecosystem a silent override means one product runs a
  provider the other never saw.
- Collapse a repeated method name into one declaration, or read an absent ``requires`` as an
  empty one. Both are refusals naming the offending path.
- Resolve a collision. Two registries sharing an id is a fact this reports and never settles.
- Carry a provider the contract will not hold. Every entry point -- JSON reader, hand-built
  dataclass, registration into a registry -- funnels through :func:`validate_registration`, so
  there is one gate rather than two that can drift.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import NoReturn

#: The contract version this module reads. Rule (1): exact, never a range -- a reader that
#: accepted an unknown version would be parsing a shape it had not seen.
PROVIDER_SCHEMA_VERSION = "iterate.provider/0.1"

#: Rule (2)/(3): the one token grammar in the contract, shared by provider id, method name
#: and every ``requires`` entry, with the same 64-character cap on all three.
KEBAB_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KEBAB_MAX_LENGTH = 64

PROVIDER_VERSION_MIN = 1
PROVIDER_VERSION_MAX = 64

#: Rule (3): the method list is 1..32; a provider offering nothing is not a provider, and
#: the cap is what keeps one registration's declaration list legible across the boundary.
PROVIDER_METHOD_MIN = 1
PROVIDER_METHOD_MAX = 32

#: Rule (3): a method's prerequisites are 0..16. Zero is reachable and means "needs
#: nothing"; the absence of the key is refused, because those are different claims.
PROVIDER_REQUIRES_MIN = 0
PROVIDER_REQUIRES_MAX = 16

#: Rule (2). The kinds are what a consumer routes on, so an unknown one cannot be carried
#: "as a hint" -- the other implementation would have nowhere to put it.
PROVIDER_KINDS = ("ae-diagnosis", "report-render", "evidence-source")

#: Rule (2). ``origin`` says whose provider this is, which is the field that makes the
#: two-harness collision question in rule (6) answerable at all.
PROVIDER_ORIGINS = ("kernel", "glasspane", "iterate", "plugin")

#: The contract's key names, as the kernel spells them. Anything outside these sets is the
#: fixture's ``extra-top-level-key`` refusal.
PROVIDER_FIELDS = frozenset(("schemaVersion", "id", "kind", "version", "origin", "methods"))
METHOD_FIELDS = frozenset(("name", "requires"))

PROVIDER_E_DUPLICATE = "PROVIDER_E_DUPLICATE"
PROVIDER_E_UNKNOWN = "PROVIDER_E_UNKNOWN"
PROVIDER_E_METHOD = "PROVIDER_E_METHOD"
PROVIDER_E_SHAPE = "PROVIDER_E_SHAPE"

#: Every code this module can raise. The test asserts each refusal's code is one of these,
#: so a fifth code cannot appear without the contract being changed in two places at once.
PROVIDER_ERROR_CODES = (
    PROVIDER_E_DUPLICATE,
    PROVIDER_E_UNKNOWN,
    PROVIDER_E_METHOD,
    PROVIDER_E_SHAPE,
)

#: The vocabulary lists, spelled the way the fixture's comment spells them, so a refusal can
#: quote the contract rather than paraphrase it.
_KINDS_TEXT = ", ".join(PROVIDER_KINDS)
_ORIGINS_TEXT = ", ".join(PROVIDER_ORIGINS)
_KEBAB_TEXT = "^[a-z0-9]+(?:-[a-z0-9]+)*$"


class ProviderRegistryError(ValueError):
    """A provider that cannot be read, registered or resolved.

    Carries ``code``/``message``/``remedy`` (and the offending ``path``) to match the
    kernel's error frames. ``message`` names the offending path verbatim, the way the
    fixture's cases spell it. The remedy is written for the agent that has to act on it
    without opening this file: it lists what *is* registered, or what the provider *does*
    offer, and says which of the two legal moves is available.
    """

    def __init__(self, code: str, message: str, remedy: str, path: str = "") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.remedy = remedy
        self.path = path


@dataclass(frozen=True)
class ProviderMethod:
    """One method a producer offers, with the prerequisites it claims it needs.

    A carrier, not the gate: ``requires`` is typed as a tuple but the runtime shape rules
    live in :func:`validate_registration`, so a half-declared method can be constructed long
    enough to be refused with the right code and path.
    """

    name: str
    requires: tuple[str, ...]

    def to_json(self) -> dict[str, object]:
        """The contract's shape: ``requires`` is always present, empty when it says empty."""
        return {"name": self.name, "requires": list(self.requires)}


@dataclass(frozen=True)
class ProviderRegistration:
    """What one judgement producer says it is and what it says it offers.

    ``schemaVersion`` is deliberately absent: it belongs to the serialised envelope, where
    only a reader can tell an absent version from a wrong one. This is the validated value,
    and it carries nothing the contract does not name.
    """

    id: str
    kind: str
    version: str
    methods: tuple[ProviderMethod, ...]
    origin: str | None = None

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schemaVersion": PROVIDER_SCHEMA_VERSION,
            "id": self.id,
            "kind": self.kind,
            "version": self.version,
            "methods": [method.to_json() for method in self.methods],
        }
        # Rule (2): origin is optional, and an explicit null is not the same claim as the
        # omission. Emit nothing when nothing was said.
        if self.origin is not None:
            out["origin"] = self.origin
        return out

    @property
    def method_names(self) -> tuple[str, ...]:
        """The declared names in declaration order -- what a caller may ask for."""
        return tuple(method.name for method in self.methods)


def _reject(path: str, detail: str, remedy: str, code: str = PROVIDER_E_SHAPE) -> NoReturn:
    """Refuse at ``path``, quoting that path in the message the way the fixture does."""
    raise ProviderRegistryError(code, f"{path} {detail}" if path else detail, remedy, path)


def _require_kebab(path: str, value: object, what: str) -> str:
    """The contract's one token grammar, applied to ids, method names and prerequisites."""
    if not isinstance(value, str):
        _reject(
            path,
            f"must be a string, got {value!r}",
            f"{what} is a string token in the contract; pass the text itself rather than a "
            "number, null or an omission",
        )
    if len(value) > KEBAB_MAX_LENGTH:
        _reject(
            path,
            f"is {len(value)} characters, over the contract's {KEBAB_MAX_LENGTH} cap",
            f"{what} is capped at {KEBAB_MAX_LENGTH} characters on both sides of the "
            f"boundary; shorten it at the source instead of letting one implementation "
            "truncate and the other refuse",
        )
    if not KEBAB_PATTERN.match(value):
        _reject(
            path,
            f"is not a lower-case kebab token: {value!r}",
            f"{what} must match {_KEBAB_TEXT} -- lower-case alphanumerics in segments joined "
            "by single hyphens, no leading or trailing hyphen, no spaces, underscores or "
            "upper case. Copy the token the producer declares instead of relabelling it here",
        )
    return value


def _require_length(path: str, value: object, minimum: int, maximum: int, what: str) -> str:
    if not isinstance(value, str):
        _reject(
            path,
            f"is required and must be a string, got {value!r}",
            f"{what} is a string the contract bounds at {minimum}..{maximum} characters; "
            "carry the value the producer states",
        )
    if not minimum <= len(value) <= maximum:
        _reject(
            path,
            f"must be {minimum}..{maximum} characters, got {len(value)}",
            f"{what} must be between {minimum} and {maximum} characters. An empty value says "
            "nothing and a longer one is a value the other implementation may truncate; "
            "state the real version rather than padding or clipping it",
        )
    return value


def _describe_prerequisites(provider: ProviderRegistration) -> str:
    """``name (requires: a, b)`` for every declaration -- what a provider actually offers."""
    return "; ".join(
        f"{method.name} (requires: {', '.join(method.requires) or 'nothing'})"
        for method in provider.methods
    )


def validate_registration(provider: ProviderRegistration) -> ProviderRegistration:
    """Refuse a registration the contract will not carry. Raises :class:`ProviderRegistryError`.

    Every rule that is a *shape* rule lives here, reached from either direction: a hand-built
    dataclass and a parsed JSON object go through the same gate, so neither can hold a value
    the other would reject. Key presence and stray keys are the exception -- a frozen
    dataclass cannot express "this key was absent" -- and are checked at the JSON boundary in
    :func:`provider_from_json`, which then calls this.

    The two refusals that carry the contract: a method name declared twice (rule 4), which
    last-write-wins would silently re-price, and a ``requires`` that is missing rather than
    empty (rule 3), which would otherwise read as a claim of independence.
    """
    _require_kebab("id", provider.id, "provider id")
    if provider.kind not in PROVIDER_KINDS:
        _reject(
            "kind",
            f"is {provider.kind!r}, outside the kinds a consumer can route",
            f"kind must be one of {_KINDS_TEXT}. A kind outside those three has no consumer "
            "on either side, so rename the provider to the kind it is rather than adding a "
            "fourth the other implementation cannot route",
        )
    _require_length(
        "version", provider.version, PROVIDER_VERSION_MIN, PROVIDER_VERSION_MAX, "provider version"
    )
    if provider.origin is not None and provider.origin not in PROVIDER_ORIGINS:
        _reject(
            "origin",
            f"is {provider.origin!r}, outside the four origins",
            f"origin must be one of {_ORIGINS_TEXT}, or omitted when the producer's side of "
            "the boundary is not what this registration is about. It is the field that says "
            "which harness owns this id, so an invented one is worse than none",
        )

    raw_methods = provider.methods
    if isinstance(raw_methods, (str, bytes, Mapping)) or not isinstance(raw_methods, Sequence):
        _reject(
            "methods",
            f"must be a sequence of method declarations, got {type(raw_methods).__name__}",
            "methods is the declaration list: 1..32 entries each carrying name and requires. "
            "A bare string or an object is not a list of declarations",
        )
    if not PROVIDER_METHOD_MIN <= len(raw_methods) <= PROVIDER_METHOD_MAX:
        _reject(
            "methods",
            f"must hold {PROVIDER_METHOD_MIN}..{PROVIDER_METHOD_MAX} entries, got "
            f"{len(raw_methods)}",
            f"a provider that declares no method offers nothing to resolve, and above "
            f"{PROVIDER_METHOD_MAX} the declaration list stops being something a consumer can "
            "check across the boundary. Split the producer into two registrations by kind "
            f"rather than truncating it to {PROVIDER_METHOD_MAX}",
        )

    methods: list[ProviderMethod] = []
    declared: dict[str, int] = {}
    for index, method in enumerate(raw_methods):
        path = f"methods.{index}"
        if not isinstance(method, ProviderMethod):
            _reject(
                path,
                f"must be a method declaration (name and requires), got "
                f"{type(method).__name__}",
                f"each entry under methods is one declaration with a kebab name and its "
                f"requires list; {path} is something else, so it cannot be checked",
            )
        name = _require_kebab(f"{path}.name", method.name, "method name")
        requires = _validate_requires(f"{path}.requires", method.requires)
        earlier = declared.get(name)
        if earlier is not None:
            _reject(
                f"{path}.name",
                f"declares {name!r} again; it is already declared at methods.{earlier}.name",
                f"methods.{earlier} declares {name} (requires: "
                f"{', '.join(methods[earlier].requires) or 'nothing'}) and {path} declares it "
                f"again (requires: {', '.join(requires) or 'nothing'}). Keep one declaration "
                f"with the prerequisites both sides agreed to, or give the second one the "
                f"name of the method it actually adds. Collapsing them -- either by taking "
                f"the last one or by intersecting the two -- would offer {name} under "
                "prerequisites nobody agreed to, which is the failure this rule exists to "
                "refuse",
                PROVIDER_E_DUPLICATE,
            )
        declared[name] = index
        methods.append(ProviderMethod(name=name, requires=requires))

    checked = ProviderRegistration(
        id=provider.id,
        kind=provider.kind,
        version=provider.version,
        methods=tuple(methods),
        origin=provider.origin,
    )
    # Return the caller's value when it already satisfied the contract; normalise a list
    # handed to a tuple-typed field, so nothing stored in a registry can be mutated later.
    return provider if checked == provider else checked


def _validate_requires(path: str, value: object) -> tuple[str, ...]:
    """Rule (3) on one method's prerequisites: required, 0..16, each a kebab token."""
    if value is None:
        _reject(
            path,
            "is required on a method and is missing",
            "every method declares its prerequisites. An empty requires is a claim the "
            "provider makes -- 'this needs nothing' -- and an absent one states nothing, so "
            f"the two cannot share a value: write {path}: [] when the method truly needs "
            "nothing, and list the signal names when it does",
        )
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        _reject(
            path,
            f"must be a sequence of kebab tokens, got {type(value).__name__}",
            f"{path} is an array of 0..{PROVIDER_REQUIRES_MAX} signal names. A bare string "
            "would iterate as single characters, and an object has no place in the contract",
        )
    if not PROVIDER_REQUIRES_MIN <= len(value) <= PROVIDER_REQUIRES_MAX:
        _reject(
            path,
            f"must hold {PROVIDER_REQUIRES_MIN}..{PROVIDER_REQUIRES_MAX} entries, got "
            f"{len(value)}",
            f"a method's prerequisites are 0..{PROVIDER_REQUIRES_MAX} named signals. Empty "
            "says 'needs nothing'; above the cap the consumer cannot satisfy the method in "
            "one round, so declare the prerequisites that gate this call and let the "
            "provider's own steps supply the rest",
        )
    return tuple(
        _require_kebab(f"{path}.{index}", item, "prerequisite name")
        for index, item in enumerate(value)
    )


@dataclass(frozen=True)
class ProviderRegistry:
    """Resolved providers by exact id: a value, replaced whole on every registration.

    Build one with :func:`empty_registry` and :func:`register_provider`. Passing a mapping to
    the constructor is legal and snapshotted here, so a dict the caller keeps mutating cannot
    change a registry after the fact -- rule (5)'s "never mutated" includes not sharing a live
    map with whoever built it.
    """

    providers: Mapping[str, ProviderRegistration]

    def __post_init__(self) -> None:
        object.__setattr__(self, "providers", MappingProxyType(dict(self.providers)))

    @property
    def ids(self) -> tuple[str, ...]:
        """Every registered id, sorted, so two harnesses can compare the same list."""
        return tuple(sorted(self.providers))

    @property
    def size(self) -> int:
        """How many providers are registered -- 0 is a value, not an error."""
        return len(self.providers)


def empty_registry() -> ProviderRegistry:
    """A registry with nothing in it. An empty registry is a value, not a failure."""
    return ProviderRegistry({})


def register_provider(
    registry: ProviderRegistry, provider: ProviderRegistration
) -> ProviderRegistry:
    """``registry`` plus ``provider``, as a NEW registry; the input is never touched.

    Two refusals come first: a registration the shape gate rejects (so a half-formed provider
    can never enter a map), and an id already present. Overwriting is refused rather than
    applied -- in a two-harness ecosystem a silent override means one product runs a provider
    the other never saw, and the returned map is the only place the decision is recorded.
    """
    checked = validate_registration(provider)
    existing = registry.providers.get(checked.id)
    if existing is not None:
        raise ProviderRegistryError(
            PROVIDER_E_DUPLICATE,
            f"a provider is already registered under {checked.id!r}: kind {existing.kind}, "
            f"version {existing.version}, offering {_describe_prerequisites(existing)}. The "
            f"incoming one is kind {checked.kind}, version {checked.version}, offering "
            f"{_describe_prerequisites(checked)}",
            f"nothing was changed: {checked.id} still resolves to the registration above, and "
            f"the incoming one was dropped. The ids registered here are: "
            f"{', '.join(registry.ids)}. If these are two different providers, give the "
            f"incoming one a distinct id before registering it. If it is meant to replace the "
            f"existing one, build the new registry explicitly -- empty_registry() plus "
            f"register_provider for each provider that should survive -- so the replacement is "
            "a decision someone can read, not a map that was written twice",
            checked.id,
        )
    merged: dict[str, ProviderRegistration] = dict(registry.providers)
    merged[checked.id] = checked
    return ProviderRegistry(merged)


def resolve_provider(registry: ProviderRegistry, provider_id: str) -> ProviderRegistration:
    """The registration under exactly ``provider_id``.

    Exact and only exact: no default provider, no prefix or fuzzy match, no "the registry has
    one provider so it must mean that one". The name is untyped on purpose -- a caller with a
    string from config is the normal case, and a lookup that guessed would let a typo resolve.
    """
    found = registry.providers.get(provider_id)
    if found is None:
        raise ProviderRegistryError(
            PROVIDER_E_UNKNOWN,
            f"no provider is registered under {provider_id!r}",
            _unregistered_remedy(registry, provider_id),
            provider_id,
        )
    # Read through the same gate that wrote it: a registry assembled by hand rather than
    # through register_provider cannot smuggle a provider nobody validated.
    return validate_registration(found)


def _unregistered_remedy(registry: ProviderRegistry, provider_id: str) -> str:
    registered = ", ".join(sorted(registry.providers))
    if not registered:
        return (
            "this registry is empty, so no id can resolve. Register the provider with "
            "register_provider(empty_registry(), provider) and pass that registry here; an "
            "unregistered provider is not the same as one that returned nothing, and no "
            "default is consulted"
        )
    return (
        f"the ids registered here are: {registered}. Resolve one of those by its exact id, "
        f"or register {provider_id} before asking for it. Nothing is defaulted and no "
        "prefix match is attempted -- guessing which provider was meant would let one "
        "product run a judgement the other never registered"
    )


def require_method(
    registry: ProviderRegistry, provider_id: str, method: str
) -> ProviderMethod:
    """The declared method on the provider at ``provider_id``, or a refusal naming them all.

    Resolution is exact first (an unregistered id is :data:`PROVIDER_E_UNKNOWN`), then the
    declaration list is read. A provider that never declared a method is not a provider that
    declined to run it, so this refuses before anything can be attempted, and the error states
    what the provider does offer under which prerequisites.
    """
    provider = resolve_provider(registry, provider_id)
    for declared in provider.methods:
        if declared.name == method:
            return declared
    raise ProviderRegistryError(
        PROVIDER_E_METHOD,
        f"provider {provider_id!r} (kind {provider.kind}) does not offer {method!r}; it "
        f"offers {', '.join(provider.method_names)}",
        f"ask {provider_id} for one of what it declares: {_describe_prerequisites(provider)}. "
        f"{method} is not among them, so either the caller means a different provider -- "
        "resolve that id instead, do not fall through -- or the producer never declared this "
        "method and registering it with its prerequisites is the change to make. Silently "
        "treating an undeclared method as unavailable is how one harness stops checking a "
        "judgement the other still runs",
        f"{provider_id}.methods",
    )


def provider_ids_in_common(a: ProviderRegistry, b: ProviderRegistry) -> list[str]:
    """Ids registered on both sides, sorted: the questions to settle before a run.

    An id both harnesses register is not necessarily a conflict -- it is a conflict when it
    means two different things, which is why this returns names and resolves nothing. Sorted
    so two implementations can compare the list byte-for-byte regardless of the order their
    maps were filled in.
    """
    return sorted(set(a.providers) & set(b.providers))


def provider_from_json(payload: object) -> ProviderRegistration:
    """Read one registration from the contract's JSON shape, then gate it.

    Key presence lives here because no frozen dataclass can express it: an absent
    ``schemaVersion``, an absent ``requires`` and an explicit null are three different claims
    that a tuple-typed field would flatten into one. Stray keys are refused rather than
    dropped -- the fixture's ``extra-top-level-key`` case exists because a key one side
    ignores and the other validates is a shape both sides think they share.
    """
    if not isinstance(payload, Mapping):
        raise ProviderRegistryError(
            PROVIDER_E_SHAPE,
            f"a provider registration must be a JSON object, got {type(payload).__name__}",
            "read it as one object carrying schemaVersion, id, kind, version, methods and "
            "optionally origin",
            "",
        )
    unknown = sorted(str(key) for key in payload if str(key) not in PROVIDER_FIELDS)
    if unknown:
        raise ProviderRegistryError(
            PROVIDER_E_SHAPE,
            f"unknown key(s) in the registration: {', '.join(unknown)}",
            "the contract's registration key set is exactly schemaVersion, id, kind, version, "
            f"methods, origin. {' and '.join(unknown)} "
            "cannot be routed by the other implementation, so carry it in your own config "
            "rather than in the shared registration; a key one side accepts and the other "
            "ignores is a shape nobody is actually validating",
            "",
        )

    version = payload.get("schemaVersion")
    if version != PROVIDER_SCHEMA_VERSION:
        _reject(
            "schemaVersion",
            f"is {version!r}, not the version this reader implements",
            f"tag the registration {PROVIDER_SCHEMA_VERSION}. An absent version is refused "
            "too, because there is no older shape this module can assume. When the kernel "
            "publishes a new version, re-vendor its fixture and update the constant here "
            "rather than accepting an unknown key set",
        )

    provider_id = _require_kebab("id", payload.get("id"), "provider id")
    raw_kind = payload.get("kind")
    if not isinstance(raw_kind, str):
        _reject(
            "kind",
            f"is required and must be a string, got {raw_kind!r}",
            f"kind is one of {_KINDS_TEXT}, as the text the producer states",
        )
    if raw_kind not in PROVIDER_KINDS:
        _reject(
            "kind",
            f"is {raw_kind!r}, outside the kinds a consumer can route",
            f"kind must be one of {_KINDS_TEXT}. A kind outside those three has no consumer "
            "on either side, so name the kind the provider is rather than inventing one",
        )
    provider_version = _require_length(
        "version",
        payload.get("version"),
        PROVIDER_VERSION_MIN,
        PROVIDER_VERSION_MAX,
        "provider version",
    )

    origin: str | None = None
    if "origin" in payload:
        raw_origin = payload["origin"]
        if raw_origin is None:
            _reject(
                "origin",
                "is present but null",
                f"either carry one of {_ORIGINS_TEXT} or leave the key out entirely; a null "
                "origin is neither 'whose provider is this' nor 'not stated'",
            )
        if not isinstance(raw_origin, str):
            _reject(
                "origin",
                f"must be a string when present, got {raw_origin!r}",
                f"origin is one of {_ORIGINS_TEXT}, as the text the producer states",
            )
        origin = raw_origin

    raw_methods = payload.get("methods")
    if not isinstance(raw_methods, list):
        _reject(
            "methods",
            f"is required and must be an array of {PROVIDER_METHOD_MIN}..{PROVIDER_METHOD_MAX} "
            f"declarations, got {raw_methods!r}",
            "methods is the declaration list: every method the provider offers, each with its "
            "name and its requires. A provider that declares nothing offers nothing to resolve",
        )

    methods: list[ProviderMethod] = []
    for index, item in enumerate(raw_methods):
        path = f"methods.{index}"
        if not isinstance(item, Mapping):
            _reject(
                path,
                f"must be a JSON object carrying name and requires, got "
                f"{type(item).__name__}",
                f"{path} is one method declaration; a bare string name cannot carry the "
                "prerequisites the contract requires",
            )
        stray = sorted(str(key) for key in item if str(key) not in METHOD_FIELDS)
        if stray:
            _reject(
                f"{path}.{stray[0]}",
                "is not a key a method declaration may carry",
                f"a method declaration is exactly name and requires; {path} also carries "
                f"{', '.join(stray)}, which the other implementation would drop or reject",
            )
        name = _require_kebab(f"{path}.name", item.get("name"), "method name")
        if "requires" not in item:
            _reject(
                f"{path}.requires",
                "is required on a method and is missing",
                f"declare {path}.requires explicitly. An empty array is the claim 'this "
                "needs nothing'; leaving the key out states nothing, and a consumer cannot "
                "tell the provider that needs no signals from the one nobody wrote down",
            )
        requires = _validate_requires(f"{path}.requires", item["requires"])
        methods.append(ProviderMethod(name=name, requires=requires))

    return validate_registration(
        ProviderRegistration(
            id=provider_id,
            kind=raw_kind,
            version=provider_version,
            methods=tuple(methods),
            origin=origin,
        )
    )
