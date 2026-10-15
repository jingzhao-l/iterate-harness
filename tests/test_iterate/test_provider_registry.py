"""The provider-registration contract's Python half, driven entirely by the kernel's fixture.

Two claims have to be falsifiable here, and they are separate. Every case in
``provider-registration.ok-01.json`` must be accepted or refused exactly as the file says, with
the refusal pointing at the field the file names -- and every expected value here comes *from
the file*, never written into this test, so editing the contract moves the assertion instead of
leaving two truths to reconcile. The second claim is about this implementation: it must refuse
the one shape the published JSON Schema cannot express (rule 4), and it must implement the
registry half -- no silent override, exact resolution, registries replaced whole rather than
mutated -- which is functions rather than schema features.

The negative controls at the bottom are not in the fixture. They hold the properties the
contract exists for and that a case list cannot show on its own: an unregistered id never
resolves to "the closest one" or "the only one", a provider is never asked for a method it did
not declare, an empty registry is a value, and the bounds the comment states are enforced even
where the file has no case for them.

The two JSON Schema tests need the ``jsonschema`` package. Without it they skip with a visible
reason rather than passing on an assertion that never ran.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from iterate_harness.iterate.provider_registry import (
    KEBAB_MAX_LENGTH,
    KEBAB_PATTERN,
    PROVIDER_E_DUPLICATE,
    PROVIDER_E_METHOD,
    PROVIDER_E_SHAPE,
    PROVIDER_E_UNKNOWN,
    PROVIDER_ERROR_CODES,
    PROVIDER_KINDS,
    PROVIDER_METHOD_MAX,
    PROVIDER_METHOD_MIN,
    PROVIDER_ORIGINS,
    PROVIDER_REQUIRES_MAX,
    PROVIDER_REQUIRES_MIN,
    PROVIDER_SCHEMA_VERSION,
    PROVIDER_VERSION_MAX,
    ProviderMethod,
    ProviderRegistration,
    ProviderRegistry,
    ProviderRegistryError,
    empty_registry,
    provider_from_json,
    provider_ids_in_common,
    register_provider,
    require_method,
    resolve_provider,
    validate_registration,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _fixture() -> dict[str, Any]:
    path = CORPUS / "provider-registration.ok-01.json"
    assert path.is_file(), (
        "provider-registration.ok-01.json is missing from the vendored corpus; restore it "
        "from iterate-kernel's fixtures/ and re-pin its sha256 in manifest.json"
    )
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), "the provider-registration fixture must be one JSON object"
    return {str(key): value for key, value in parsed.items()}


#: Read at import, because parametrisation needs the cases before any test body runs.
FIXTURE: dict[str, Any] = _fixture()
COMMENT: str = str(FIXTURE["comment"])
CASES: list[dict[str, Any]] = [dict(case) for case in FIXTURE["cases"]]
REGISTRY: dict[str, Any] = {str(key): value for key, value in FIXTURE["registry"].items()}
COLLISION: dict[str, Any] = {str(key): value for key, value in REGISTRY["collision"].items()}
INPUT: dict[str, Any] = {str(key): value for key, value in FIXTURE["input"].items()}
EXPECTED_METHODS: list[str] = [str(name) for name in FIXTURE["expectedMethods"]]


def _case_id(case: dict[str, Any]) -> str:
    return str(case["name"])


def _providers_of(group: str) -> list[Any]:
    """One of the fixture's registry-side provider lists, as the file spells it."""
    entries: list[Any] = list(REGISTRY[group])
    assert entries, f"the fixture's {group} array is empty; nothing was pinned"
    return entries


def _ids_of(entries: list[Any]) -> list[str]:
    return [str(dict(entry)["id"]) for entry in entries]


def _declarations(entry: Any) -> list[dict[str, Any]]:
    return [dict(method) for method in dict(entry)["methods"]]


def _registry_from(entries: list[Any]) -> ProviderRegistry:
    """A registry built the way the contract says to build one: empty, then registered."""
    registry = empty_registry()
    for entry in entries:
        registry = register_provider(registry, provider_from_json(entry))
    return registry


def _hand_built(payload: dict[str, Any]) -> ProviderRegistration:
    """One registration assembled straight into the dataclasses, past the JSON reader.

    Only the contract's own keys are read, so no shape rule can be exercised by a field this
    helper invented. The gate under test is ``validate_registration``, not a translation layer.
    """
    methods = tuple(
        ProviderMethod(
            name=str(method["name"]),
            requires=tuple(str(token) for token in method["requires"]),
        )
        for method in payload["methods"]
    )
    origin = payload.get("origin")
    return ProviderRegistration(
        id=str(payload["id"]),
        kind=str(payload["kind"]),
        version=str(payload["version"]),
        methods=methods,
        origin=str(origin) if origin is not None else None,
    )


def _refused(payload: dict[str, Any]) -> ProviderRegistryError:
    """The error the contract says this registration must raise, read back as an error."""
    with pytest.raises(ProviderRegistryError) as caught:
        provider_from_json(payload)
    error: ProviderRegistryError = caught.value
    return error


def _repeats_a_method_name(payload: dict[str, Any]) -> bool:
    """Whether the file's own declaration list repeats a name -- rule (4), read from data.

    The fixture pins each refusal's path and expectation but not its error code, so which code
    a case must carry is derived here from the shape rather than from a table this file keeps.
    """
    methods = payload.get("methods")
    if not isinstance(methods, list):
        return False
    names = [str(dict(method).get("name")) for method in methods]
    return len(names) != len(set(names))


# --------------------------------------------------------------------------- the input ---


def test_the_case_list_is_populated_and_states_an_expectation() -> None:
    """Guard: the parametrisation below cannot go green by covering nothing."""
    assert CASES, "the fixture's cases array is empty; nothing was pinned"
    stated = [str(case["expect"]) for case in CASES]
    assert set(stated) <= {"accepted", "refused"}, f"unknown expectations in {stated}"


def test_the_pinned_input_validates_and_declares_the_expected_methods() -> None:
    """The worked example at the head of the fixture, gated directly and in order.

    ``validate_registration`` is called on a hand-built registration rather than only through
    the JSON reader, so the gate itself is what is exercised here. The names are checked against
    ``expectedMethods`` as a list, in order, because declaration order is part of what the file
    pins.
    """
    provider = validate_registration(_hand_built(INPUT))
    assert [method.name for method in provider.methods] == EXPECTED_METHODS
    assert provider == _hand_built(INPUT), "validation must not re-price a legal registration"
    assert provider_from_json(INPUT) == provider
    assert provider.to_json() == INPUT, (
        "reading and re-emitting must be lossless: an absent origin stays absent and every "
        "declared requires list stays present, even when it says empty"
    )
    assert INPUT["schemaVersion"] == PROVIDER_SCHEMA_VERSION


@pytest.mark.parametrize("case", CASES, ids=_case_id)
def test_every_pinned_case_is_accepted_or_refused_as_stated(case: dict[str, Any]) -> None:
    expect = str(case["expect"])
    path = str(case.get("path", ""))

    if expect == "refused":
        error = _refused(case["input"])
        assert error.code in PROVIDER_ERROR_CODES, f"undeclared code {error.code!r}"
        assert error.code == (
            PROVIDER_E_DUPLICATE if _repeats_a_method_name(case["input"]) else PROVIDER_E_SHAPE
        ), (
            "a repeated method name is rule (4)'s duplicate; every other shape refusal is a "
            "shape failure"
        )
        assert error.path == path, "the refusal must point at the field the fixture names"
        assert error.remedy.strip(), f"{case['name']}: a refusal needs an actionable remedy"
        if path:
            assert path in error.message, f"{path} not named in: {error.message}"
        if "messageIncludes" in case:
            includes = str(case["messageIncludes"])
            assert includes in error.message, f"{includes} not named in: {error.message}"
        return

    provider = provider_from_json(case["input"])
    assert validate_registration(provider) == provider
    assert provider.to_json() == case["input"]
    assert [method.name for method in provider.methods] == [
        str(method["name"]) for method in case["input"]["methods"]
    ]


# ----------------------------------------------------------------------- rule (4), twice ---


def test_a_repeated_method_name_is_refused_even_when_the_two_declarations_agree() -> None:
    """The half of rule (4) the fixture's single duplicate case cannot show.

    There, the two declarations differ by ``requires``. Here they are identical, which is
    exactly the shape an implementation that collapses duplicates swallows into one entry and
    reports green -- so the refusal must not depend on the two disagreeing.
    """
    declarations = _declarations(INPUT)
    assert len(declarations) > 1
    repeated = {**INPUT, "methods": [declarations[0], declarations[0]]}
    error = _refused(repeated)
    assert error.code == PROVIDER_E_DUPLICATE
    assert error.path == "methods.1.name", "the second declaration is the one refused"
    assert EXPECTED_METHODS[0] in error.message


def test_the_duplicate_refusal_names_both_declarations_it_is_comparing() -> None:
    """The remedy has to be actable on without opening this source.

    A caller told only "duplicate name" cannot tell which prerequisites survived, so the
    refusal quotes the earlier index and both ``requires`` lists -- the whole reason the
    contract refuses instead of collapsing.
    """
    pinned = next(case for case in CASES if _repeats_a_method_name(case["input"]))
    error = _refused(pinned["input"])
    declared = _declarations(pinned["input"])
    assert str(declared[0]["name"]) in error.message
    assert "methods.0.name" in error.message
    for index, method in enumerate(declared):
        for token in method["requires"]:
            assert str(token) in error.remedy, (
                f"the refusal must show what methods.{index} would have priced in: {token}"
            )


# ------------------------------------------------------------------ constants vs comment ---


def test_the_constants_are_the_values_the_fixture_prose_states() -> None:
    """The rules implemented are the comment's, not this file's.

    Each bound is quoted back out of ``comment``, so a change to the contract's numbers fails
    here rather than becoming a silent re-tune of the implementation.
    """
    assert PROVIDER_SCHEMA_VERSION in COMMENT
    assert KEBAB_PATTERN.pattern in COMMENT
    assert "|".join(PROVIDER_KINDS) in COMMENT
    assert "|".join(PROVIDER_ORIGINS) in COMMENT
    assert f"1..{PROVIDER_METHOD_MAX} entries" in COMMENT
    assert f"0..{PROVIDER_REQUIRES_MAX} entries" in COMMENT
    assert f"1..{PROVIDER_VERSION_MAX} chars" in COMMENT
    assert f"<={KEBAB_MAX_LENGTH} chars" in COMMENT


def test_every_registration_in_the_file_uses_the_declared_vocabulary() -> None:
    """The three kinds and four origins, checked against what the corpus actually contains.

    Only the shapes the file expects to *survive* are collected: the ``unknown-kind`` case
    exists precisely to carry a kind outside the vocabulary, and a refusal must not be able to
    make the vocabulary list wrong.
    """
    blobs: list[dict[str, Any]] = [INPUT]
    blobs.extend(
        dict(case["input"]) for case in CASES if str(case["expect"]) == "accepted"
    )
    blobs.extend(dict(entry) for entry in _providers_of("glasspane"))
    blobs.extend(dict(entry) for entry in _providers_of("iterate"))
    blobs.extend(dict(entry) for entry in COLLISION["a"])
    blobs.extend(dict(entry) for entry in COLLISION["b"])
    assert all(blob["schemaVersion"] == PROVIDER_SCHEMA_VERSION for blob in blobs)
    kinds = {str(blob["kind"]) for blob in blobs}
    assert kinds <= set(PROVIDER_KINDS)
    assert kinds == set(PROVIDER_KINDS), "a kind the corpus never uses is a kind nothing tests"
    origins = {str(blob["origin"]) for blob in blobs if "origin" in blob}
    assert origins <= set(PROVIDER_ORIGINS)
    # The one kind the corpus carries outside the vocabulary arrives in a refused case only,
    # which is the fixture's own way of saying the list above is the whole set.
    refused_kinds = {
        str(dict(case["input"])["kind"])
        for case in CASES
        if str(case["expect"]) == "refused"
    } - kinds
    assert refused_kinds, "no refused case tests a kind outside the vocabulary"


# ---------------------------------------------------------- the JSON Schema divergence ---


def _schema_engine() -> Any:
    """``jsonschema`` or a visible skip -- the schema half asserts nothing without it."""
    engine: Any = pytest.importorskip(
        "jsonschema",
        reason=(
            "the published schema's side of the documented divergence needs the jsonschema "
            "package, which is not installed in this environment; skipped, not assumed"
        ),
    )
    return engine


def _published_schema() -> dict[str, Any]:
    """Rules (1)-(3) of the fixture's comment, transcribed to JSON Schema keywords.

    Every bound is read from the module's own constants, so the schema cannot be a strawman and
    cannot drift from the validator: the only room left for the two to disagree is what the
    comment says a schema cannot say. ``uniqueItems`` is included because it is the closest
    feature JSON Schema has -- and it compares whole objects, so two declarations differing only
    in ``requires`` are not duplicate items.
    """
    token: dict[str, Any] = {
        "type": "string",
        "pattern": KEBAB_PATTERN.pattern,
        "maxLength": KEBAB_MAX_LENGTH,
    }
    declaration: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "requires"],
        "properties": {
            "name": dict(token),
            "requires": {
                "type": "array",
                "minItems": PROVIDER_REQUIRES_MIN,
                "maxItems": PROVIDER_REQUIRES_MAX,
                "items": dict(token),
            },
        },
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["schemaVersion", "id", "kind", "version", "methods"],
        "properties": {
            "schemaVersion": {"const": PROVIDER_SCHEMA_VERSION},
            "id": dict(token),
            "kind": {"enum": list(PROVIDER_KINDS)},
            "version": {
                "type": "string",
                "minLength": PROVIDER_METHOD_MIN,
                "maxLength": PROVIDER_VERSION_MAX,
            },
            "origin": {"enum": list(PROVIDER_ORIGINS)},
            "methods": {
                "type": "array",
                "minItems": PROVIDER_METHOD_MIN,
                "maxItems": PROVIDER_METHOD_MAX,
                "uniqueItems": True,
                "items": declaration,
            },
        },
    }
    return schema


def _schema_accepts(engine: Any, payload: dict[str, Any]) -> bool:
    validator = engine.Draft202012Validator(_published_schema())
    accepted: bool = validator.is_valid(payload)
    return accepted


def test_the_documented_divergence_is_a_shape_the_schema_accepts_and_we_refuse() -> None:
    """Rule (4)'s last sentence, checked against both sides instead of asserted.

    The fixture marks one case ``jsonSchemaAccepts``: the published schema takes it, the
    validator refuses it. That is only a divergence if the schema really does accept it, so the
    schema side is evaluated here rather than trusted -- and the contract's own worked example
    has to satisfy the same schema, or that schema would be a strawman.
    """
    engine = _schema_engine()
    divergent = [case for case in CASES if case.get("jsonSchemaAccepts") is True]
    assert divergent, "the fixture documents no schema divergence; this test is stale"
    for case in divergent:
        assert str(case["name"]) in COMMENT, "the comment names the divergent case by name"
        assert str(case["expect"]) == "refused"
        assert _schema_accepts(engine, dict(case["input"])), (
            f"{case['name']}: the published schema must accept what the validator refuses, or "
            "this is not the documented divergence"
        )
        error = _refused(case["input"])
        assert error.code == PROVIDER_E_DUPLICATE
        assert error.path == str(case["path"])
    assert _schema_accepts(engine, INPUT), (
        "the transcription of rules (1)-(3) rejects the contract's own example, so every "
        "accepts/refuses claim here would prove nothing"
    )


def test_the_schema_agrees_with_us_on_every_case_it_documents_no_divergence_for() -> None:
    """The divergence is exactly one case wide, not a general gap between the two sides.

    For each refused case the file does not mark ``jsonSchemaAccepts``, the schema must refuse it
    too -- those rules are expressible in schema keywords, so being stricter than the schema
    there is a bug rather than the contract. And each accepted case must satisfy the schema, or
    this validator accepts shapes the published contract does not describe.
    """
    engine = _schema_engine()
    documented = sorted(str(case["name"]) for case in CASES if case.get("jsonSchemaAccepts"))
    schema_takes_refusals = sorted(
        str(case["name"])
        for case in CASES
        if case["expect"] == "refused" and _schema_accepts(engine, dict(case["input"]))
    )
    assert schema_takes_refusals == documented, (
        f"the schema accepts refused cases {schema_takes_refusals}, but the file documents "
        f"only {documented} as a divergence"
    )
    for case in CASES:
        if case["expect"] == "accepted":
            assert _schema_accepts(engine, dict(case["input"])), (
                f"{case['name']}: accepted by this validator, refused by the published schema"
            )


# ----------------------------------------------------------------------- the registry ---


def test_every_provider_the_file_registers_resolves_by_its_exact_id() -> None:
    """Both harness registries, read back through the registry API field for field."""
    for group in ("glasspane", "iterate"):
        entries = _providers_of(group)
        registry = _registry_from(entries)
        assert registry.size == len(entries)
        assert sorted(registry.providers) == sorted(_ids_of(entries))
        for entry in entries:
            payload = dict(entry)
            provider = resolve_provider(registry, str(payload["id"]))
            assert provider.kind == payload["kind"]
            assert provider.version == payload["version"]
            assert provider.origin == payload.get("origin")
            assert provider.method_names == tuple(
                str(method["name"]) for method in _declarations(payload)
            )
            assert provider.to_json() == payload


def test_the_two_registries_share_the_ids_the_file_lists() -> None:
    """Rule (6), the benign half: having nothing in common is also a fact worth pinning."""
    glasspane = _registry_from(_providers_of("glasspane"))
    iterate = _registry_from(_providers_of("iterate"))
    assert provider_ids_in_common(glasspane, iterate) == REGISTRY["idsInCommon"]
    assert provider_ids_in_common(iterate, glasspane) == REGISTRY["idsInCommon"]


def _shared_method_names(provider_id: str) -> list[str]:
    """Method names declared under ``provider_id`` on both sides of the collision.

    Only names declared on both sides can be compared for prerequisites; a name one side never
    declared is a :data:`PROVIDER_E_METHOD` refusal, which is a different claim.
    """
    left = {
        str(method["name"])
        for entry in COLLISION["a"]
        if str(entry["id"]) == provider_id
        for method in _declarations(entry)
    }
    right = {
        str(method["name"])
        for entry in COLLISION["b"]
        if str(entry["id"]) == provider_id
        for method in _declarations(entry)
    }
    assert left == right, (
        f"{provider_id} declares different method names per side; this comparison is about "
        "prerequisites, and a missing name is a refusal rather than a difference"
    )
    return sorted(left)


def _declared_requires(entries: list[Any], provider_id: str, method: str) -> tuple[str, ...]:
    for entry in entries:
        payload = dict(entry)
        if str(payload["id"]) == provider_id:
            for declaration in _declarations(payload):
                if str(declaration["name"]) == method:
                    return tuple(str(token) for token in declaration["requires"])
    raise AssertionError(f"{method} is not declared under {provider_id} in the fixture")


def test_the_collision_pair_is_reported_and_never_resolved() -> None:
    """The state the contract exists to catch, read out of the two registries.

    One shared id resolves to *different* prerequisites in each product -- which is how both
    could look satisfied while running different judgements. This pins that the shared ids are
    exactly what the file expects, that each side still answers with its own declaration, and
    that the id the collision note calls out is the id whose prerequisites actually differ.
    """
    a = _registry_from(COLLISION["a"])
    b = _registry_from(COLLISION["b"])
    shared = provider_ids_in_common(a, b)
    assert shared == COLLISION["expect"]
    assert shared == sorted(shared), "the ids to settle must compare the same way on both sides"

    diverging: list[tuple[str, str]] = []
    for provider_id in shared:
        for method in _shared_method_names(provider_id):
            on_a = require_method(a, provider_id, method)
            on_b = require_method(b, provider_id, method)
            assert on_a.requires == _declared_requires(COLLISION["a"], provider_id, method)
            assert on_b.requires == _declared_requires(COLLISION["b"], provider_id, method)
            if on_a.requires != on_b.requires:
                diverging.append((provider_id, method))

    assert diverging, (
        "both registries offer identical prerequisites for every shared id, so there is "
        "nothing here for provider_ids_in_common to report"
    )
    note = str(COLLISION["note"])
    named = sorted({provider_id for provider_id in shared if provider_id in note})
    assert named == sorted({provider_id for provider_id, _ in diverging}), (
        "the ids the collision note calls out must be exactly the ids whose prerequisites "
        "differ across the boundary"
    )


def test_re_registering_a_present_id_is_refused_as_a_duplicate_not_an_override() -> None:
    """Rule (5): last-write-wins is the failure, so the code has to say duplicate."""
    entries = _providers_of("glasspane")
    registry = _registry_from(entries)
    first = dict(entries[0])
    incoming = provider_from_json(first)

    with pytest.raises(ProviderRegistryError) as caught:
        register_provider(registry, incoming)
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_DUPLICATE
    assert error.path == str(first["id"])
    assert str(first["id"]) in error.message
    assert str(first["kind"]) in error.message
    assert str(first["version"]) in error.message
    for declaration in _declarations(first):
        assert str(declaration["name"]) in error.message
    for provider_id in _ids_of(entries):
        assert provider_id in error.remedy, (
            f"the remedy must list {provider_id}, so the caller can see what is registered "
            "without reading this source"
        )
    assert registry.size == len(entries), "the refused registration changed nothing"
    assert resolve_provider(registry, str(first["id"])) == incoming


def test_registration_returns_a_new_registry_and_mutates_nothing() -> None:
    """Rule (5)'s mechanical half: a registry is replaced whole, never written into.

    A half-applied registration would leave one map with the provider and the next without it,
    so every function returns a value and leaves its input alone -- including a registry built
    from a mapping its caller still holds.
    """
    entries = _providers_of("glasspane")
    base = _registry_from(entries[:1])
    before = dict(base.providers)
    second = provider_from_json(entries[1])
    grown = register_provider(base, second)

    assert base.providers == before, "the registry handed in must be untouched"
    assert grown is not base
    assert base.size == 1 and grown.size == 2
    assert list(base.providers) == _ids_of(entries)[:1]
    assert sorted(grown.providers) == sorted(_ids_of(entries))

    source: dict[str, ProviderRegistration] = {}
    held = ProviderRegistry(source)
    source[second.id] = second
    assert held.size == 0, (
        "a registry must not share a live map with whoever built it, or a later write to that "
        "dict changes what resolves without anyone registering anything"
    )


# -------------------------------------------------------------- negative controls (shape) ---


def test_the_bounds_the_comment_states_are_enforced_where_the_file_has_no_case() -> None:
    """Long ids, long versions, oversized lists, an invented origin.

    The corpus has a case for the empty end of several of these and none for the far end, so
    the caps are pinned against the module's own constants rather than against literals.
    """
    base = provider_from_json(INPUT)
    oversized: list[tuple[str, Any]] = [
        ("id", "a" * (KEBAB_MAX_LENGTH + 1)),
        ("version", "v" * (PROVIDER_VERSION_MAX + 1)),
        ("origin", "everywhere"),
    ]
    for field, value in oversized:
        with pytest.raises(ProviderRegistryError) as caught:
            validate_registration(replace(base, **{field: value}))
        error: ProviderRegistryError = caught.value
        assert error.code == PROVIDER_E_SHAPE
        assert error.path == field
        assert field in error.message
        assert error.remedy.strip()

    over_cap = tuple(
        ProviderMethod(name=f"method-{index}", requires=())
        for index in range(PROVIDER_METHOD_MAX + 1)
    )
    with pytest.raises(ProviderRegistryError) as caught:
        validate_registration(replace(base, methods=over_cap))
    assert caught.value.path == "methods"
    assert f"{PROVIDER_METHOD_MIN}..{PROVIDER_METHOD_MAX}" in str(caught.value)

    stretched = tuple(f"signal-{index}" for index in range(PROVIDER_REQUIRES_MAX + 1))
    with pytest.raises(ProviderRegistryError) as caught:
        validate_registration(
            replace(base, methods=(ProviderMethod(name="diagnose", requires=stretched),))
        )
    assert caught.value.path == "methods.0.requires"
    assert f"{PROVIDER_REQUIRES_MIN}..{PROVIDER_REQUIRES_MAX}" in str(caught.value)

    # Exactly at the cap is legal: the refusals above must not be off-by-one artefacts.
    at_cap = tuple(
        ProviderMethod(
            name=f"method-{index}",
            requires=tuple(f"signal-{step}" for step in range(PROVIDER_REQUIRES_MAX)),
        )
        for index in range(PROVIDER_METHOD_MAX)
    )
    checked = validate_registration(replace(base, methods=at_cap))
    assert len(checked.methods) == PROVIDER_METHOD_MAX
    assert len(checked.methods[0].requires) == PROVIDER_REQUIRES_MAX


def test_a_missing_requires_is_a_shape_failure_not_an_empty_one() -> None:
    """Rule (3)'s distinction, reached through both doors the contract has.

    JSON can say ``requires`` is absent and a tuple-typed field cannot, so the reader refuses it;
    and a caller who writes ``None`` into the dataclass -- Python's spelling of "not said" -- is
    refused by the same gate rather than read as "needs nothing".
    """
    pinned = next(case for case in CASES if str(case.get("path", "")).endswith(".requires"))
    error = _refused(pinned["input"])
    assert error.path == str(pinned["path"])
    assert "required" in error.message

    absent: Any = None
    with pytest.raises(ProviderRegistryError) as caught:
        validate_registration(
            replace(
                provider_from_json(INPUT),
                methods=(ProviderMethod(name=str(EXPECTED_METHODS[0]), requires=absent),),
            )
        )
    refusal: ProviderRegistryError = caught.value
    assert refusal.code == PROVIDER_E_SHAPE
    assert refusal.path == "methods.0.requires"
    assert "[]" in refusal.remedy, (
        "the remedy has to say how to state 'needs nothing' instead of leaving it out"
    )

    # Whereas the empty form is a claim the contract carries, not a refusal.
    stated = [{"name": EXPECTED_METHODS[0], "requires": []}]
    empty_requires = provider_from_json({**INPUT, "methods": stated})
    assert empty_requires.methods[0].requires == ()
    assert empty_requires.to_json()["methods"] == stated


def test_an_absent_or_foreign_schema_version_is_refused() -> None:
    """Rule (1), at the one door that can tell an absent version from a wrong one."""
    untagged = {key: value for key, value in INPUT.items() if key != "schemaVersion"}
    error = _refused(untagged)
    assert error.code == PROVIDER_E_SHAPE
    assert error.path == "schemaVersion"
    assert PROVIDER_SCHEMA_VERSION in error.remedy

    for version in ("iterate.provider/0.2", "iterate.run-plan/0.1"):
        assert _refused({**INPUT, "schemaVersion": version}).path == "schemaVersion"


# --------------------------------------------------------- negative controls (registry) ---


def test_an_unregistered_id_is_refused_and_the_remedy_lists_what_is_registered() -> None:
    """Rule (5): no default, no fuzzy match, no "the only one registered".

    The near miss is a registered id with its last segment dropped, taken from the file, so this
    is a real prefix collision rather than a made-up string.
    """
    entries = _providers_of("glasspane")
    registry = _registry_from(entries)
    registered = _ids_of(entries)
    near_miss = registered[0].rsplit("-", 1)[0]
    assert near_miss not in registered

    for missing in (near_miss, "no-such-provider", registered[0].upper()):
        with pytest.raises(ProviderRegistryError) as caught:
            resolve_provider(registry, missing)
        error: ProviderRegistryError = caught.value
        assert error.code == PROVIDER_E_UNKNOWN
        assert missing in error.message
        for provider_id in registered:
            assert provider_id in error.remedy, (
                f"the remedy must name {provider_id} so the caller can pick the real id"
            )

    # A registry holding one provider still refuses everything that is not that provider.
    alone = _registry_from(entries[:1])
    with pytest.raises(ProviderRegistryError) as caught:
        resolve_provider(alone, registered[1])
    assert caught.value.code == PROVIDER_E_UNKNOWN
    assert registered[0] in caught.value.remedy

    # And the same exactness one level down: an unregistered id is not a bad method name.
    with pytest.raises(ProviderRegistryError) as caught:
        require_method(alone, near_miss, EXPECTED_METHODS[0])
    assert caught.value.code == PROVIDER_E_UNKNOWN


def test_a_method_the_provider_does_not_offer_is_refused_and_names_what_it_offers() -> None:
    """Rule (5): the refusal has to say what the provider actually declares.

    The method asked for is one the *other* harness's provider declares, read from the file --
    the realistic mistake, and the one a registry that fell through to another provider would
    make invisible.
    """
    glasspane = _registry_from(_providers_of("glasspane"))
    first = dict(_providers_of("glasspane")[0])
    offered = [str(method["name"]) for method in _declarations(first)]
    asked = str(_declarations(dict(_providers_of("iterate")[0]))[0]["name"])
    assert asked not in offered

    with pytest.raises(ProviderRegistryError) as caught:
        require_method(glasspane, str(first["id"]), asked)
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_METHOD
    assert error.path == f"{first['id']}.methods"
    assert asked in error.message
    assert str(first["id"]) in error.message
    for name in offered:
        assert name in error.remedy, f"the remedy must list {name}, what this provider offers"
    for method in _declarations(first):
        for token in method["requires"]:
            assert str(token) in error.remedy, (
                "what is offered has to be quoted with the prerequisites it is offered under"
            )

    # The offered methods do resolve, each with the file's own prerequisites.
    for name, method in zip(offered, _declarations(first), strict=True):
        found = require_method(glasspane, str(first["id"]), name)
        assert found.name == name
        assert found.requires == tuple(str(token) for token in method["requires"])


def test_the_empty_registry_is_a_value_not_an_error() -> None:
    """Nothing registered is a state, and reading it must say so rather than crash."""
    empty = empty_registry()
    assert empty.size == 0
    assert list(empty.providers) == []
    assert provider_ids_in_common(empty, empty) == []
    assert provider_ids_in_common(empty, _registry_from(_providers_of("glasspane"))) == []

    with pytest.raises(ProviderRegistryError) as caught:
        resolve_provider(empty, str(INPUT["id"]))
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_UNKNOWN
    assert "empty" in error.remedy

    with pytest.raises(ProviderRegistryError) as caught:
        require_method(empty, str(INPUT["id"]), EXPECTED_METHODS[0])
    assert caught.value.code == PROVIDER_E_UNKNOWN

    # And it is a starting value: registering into it is how every other test began.
    assert register_provider(empty, provider_from_json(INPUT)).size == 1
