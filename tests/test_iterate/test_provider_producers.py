"""This harness's providers are registered through the contract, and something consumes them.

:mod:`.provider_registry` is a ruler: it can read anybody's registration and registers nobody.
Scenario 5 of specs 8.5/R40 is only delivered once *this* product holds a registry and a real
call path asks it a question. Two claims are therefore falsified here, separately.

1. **The registration is the corpus's, and the version is ours.** ``id``, ``kind``, ``method``
   and ``requires`` come out of ``provider-registration.ok-01.json``'s ``registry.iterate``
   block byte-for-byte -- read out of the file, never retyped into this test. ``version`` must
   equal what the package's own version source states at runtime, and the test proves that by
   mutating the source, because the fixture's comment says its version strings are real
   measurements of a build and a product restating them would certify a claim it never made.
2. **The registry refuses the way the contract says.** A duplicate in one build, an
   unregistered id, an undeclared method, a material that carries none of a method's
   prerequisites -- each is a refusal naming what *is* registered, never a fall-through.

Then the consumers: the evidence-pack relay will not transcribe a verdict from a pack the
registered AE provider will not price, and the Markdown renderer will not publish a report from
a decision log that holds nothing. Both refusals are reachable, and both are shown reaching.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

import iterate_harness
from iterate_harness.iterate import pr_comment, provider_producers, provider_registry
from iterate_harness.iterate.ci_report import ReportSummary
from iterate_harness.iterate.decision_log import make_entry
from iterate_harness.iterate.glasspane_reverification import ReverificationRun, relay_answer
from iterate_harness.iterate.pr_comment import PostResult, render_markdown
from iterate_harness.iterate.provider_registry import (
    PROVIDER_E_DUPLICATE,
    PROVIDER_E_METHOD,
    PROVIDER_E_UNKNOWN,
    ProviderRegistryError,
    require_method,
    resolve_provider,
)

CORPUS = Path(__file__).resolve().parents[1] / "kernel_fixtures"


def _fixture() -> dict[str, Any]:
    path = CORPUS / "provider-registration.ok-01.json"
    assert path.is_file(), "the provider-registration fixture is missing from the vendored corpus"
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return {str(key): value for key, value in parsed.items()}


FIXTURE: dict[str, Any] = _fixture()
REGISTRY_BLOCK: dict[str, Any] = {str(k): v for k, v in FIXTURE["registry"].items()}
ITERATE_ENTRIES: list[dict[str, Any]] = [dict(e) for e in REGISTRY_BLOCK["iterate"]]
GLASSPANE_ENTRIES: list[dict[str, Any]] = [dict(e) for e in REGISTRY_BLOCK["glasspane"]]
COLLISION: dict[str, Any] = {str(k): v for k, v in REGISTRY_BLOCK["collision"].items()}
COMMENT: str = str(FIXTURE["comment"])


def _as_registered(provider_id: str) -> Any:
    return resolve_provider(provider_producers.iterate_providers(), provider_id)


def _payload_of(provider_id: str) -> dict[str, Any]:
    for entry in ITERATE_ENTRIES:
        if str(entry["id"]) == provider_id:
            return entry
    raise AssertionError(f"the fixture's iterate block does not name {provider_id}")


# --------------------------------------------------------------- the registry this product builds ---


def test_both_producers_resolve_by_exact_id_and_match_the_corpus_declaration() -> None:
    """The shape is the corpus's, field for field -- with the version handled separately.

    Comparing ``to_json()`` after swapping in the fixture's own version string keeps this a
    test of *this product's* registration rather than of the reader that built it.
    """
    for entry in ITERATE_ENTRIES:
        provider = _as_registered(str(entry["id"]))
        assert provider.kind == entry["kind"]
        assert provider.origin == entry["origin"]
        assert provider.to_json() == {**entry, "version": provider.version}


def test_registered_ids_are_exactly_the_two_the_corpus_names_for_this_harness() -> None:
    assert list(provider_producers.iterate_providers().ids) == sorted(
        str(entry["id"]) for entry in ITERATE_ENTRIES
    )


def test_the_declared_requires_vocabulary_equals_the_fixture_s_iterate_requires() -> None:
    """The prerequisite tokens this module derives are the ones the corpus declares.

    ``provider_producers`` holds the engine's camelCase field names and renders the kebab
    tokens by function; that is a restatement in a different spelling, so the equality is
    pinned here rather than trusted.
    """
    ours: set[str] = set()
    for entry in provider_producers.producer_payloads():
        for method in entry["methods"]:
            ours.update(str(token) for token in method["requires"])
    theirs: set[str] = set()
    for entry in ITERATE_ENTRIES:
        for method in entry["methods"]:
            theirs.update(str(token) for token in method["requires"])
    assert ours == theirs
    assert ours <= set(provider_producers.EVIDENCE_SIGNAL_TOKENS) | {
        provider_producers.EVIDENCE_PACK_TOKEN,
        provider_producers.DECISION_LOG_TOKEN,
    }


def test_the_vocabulary_is_rendered_from_the_engine_s_field_names_not_retyped() -> None:
    """Every kebab token this module can produce is a rendering of a real pack field.

    The restatement risk is the whole reason for the test: a literal list of ``requires``
    tokens copied from the corpus would drift the moment the engine renamed a signal, and
    nothing would notice. So the field names are checked against an archived pack the engine
    really wrote, and the kebab forms against the renderings the corpus comment names.
    """
    pack = _real_pack()
    engine_fields = set(pack) | set(pack.get("signals") or {})
    assert set(provider_producers.EVIDENCE_PACK_FIELDS) <= engine_fields, (
        "EVIDENCE_PACK_FIELDS must be the engine's own field names, read from a real pack"
    )
    for field in ("axEvent", "handlerProbe", "stateDiff", "pixelDiff", "responsiveness", "crash"):
        assert f"{field}/" in COMMENT or f"/{field}" in COMMENT, (
            f"{field} is not one of the SignalsSchema keys the corpus comment renders in kebab case"
        )
    assert provider_producers._kebab("circuitBreaker") == "circuit-breaker"
    assert provider_producers.EVIDENCE_PACK_TOKEN == "evidence-pack"
    assert provider_producers.DECISION_LOG_TOKEN == "decision-log"

    producible = set(provider_producers.EVIDENCE_SIGNAL_TOKENS) | {
        provider_producers.EVIDENCE_PACK_TOKEN,
        provider_producers.DECISION_LOG_TOKEN,
    }
    declared: set[str] = set()
    for entry in ITERATE_ENTRIES:
        for method in entry["methods"]:
            declared.update(str(token) for token in method["requires"])
    assert declared <= producible, f"the corpus declares {sorted(declared - producible)} unreadably"


# ---------------------------------------------------------------------------------------- version ---


def test_the_registered_version_is_the_product_s_own_and_not_the_fixture_s() -> None:
    """The version comes from the same source the wheel and ``--version`` read.

    Reading it through ``iterate_harness.__version__`` here -- rather than a literal -- is what
    makes a module that hardcoded a version fail this test.
    """
    source = importlib.import_module("iterate_harness").__version__
    for entry in provider_producers.producer_payloads():
        assert str(entry["version"]) == str(source)
    for provider_id in provider_producers.iterate_providers().ids:
        assert _as_registered(provider_id).version == str(source)
    # The fixture's version strings are its own measurements of a build ("the package/engine
    # versions at the time of writing, not placeholders"), so this product registering the same
    # number it never shipped would be certifying a claim it did not make.
    for entry in ITERATE_ENTRIES:
        assert str(source) != str(entry["version"]), (
            f"{entry['id']}: the registered version is the fixture's {entry['version']!r}, "
            "which is a claim about a build this package did not measure"
        )


def test_mutating_the_version_source_moves_what_gets_registered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: nothing about the version is baked into the module.

    A hard-coded registration would keep reporting the old version after this patch and the
    assertion above would be a tautology.
    """
    monkeypatch.setattr(iterate_harness, "__version__", "9.9.9-mutation-probe")
    assert provider_producers.product_version() == "9.9.9-mutation-probe"
    for provider_id in provider_producers.iterate_providers().ids:
        assert _as_registered(provider_id).version == "9.9.9-mutation-probe"


# ------------------------------------------------------------------------------ registry refusals ---


def test_a_duplicate_id_in_one_build_is_refused_and_changes_nothing() -> None:
    """The builder cannot smuggle a second registration past the contract's no-override rule."""
    payloads = provider_producers.producer_payloads()
    assert len(payloads) == 2
    with pytest.raises(ProviderRegistryError) as caught:
        provider_producers.build_registry([*payloads, payloads[1]])
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_DUPLICATE
    assert error.path == provider_producers.RENDER_PROVIDER_ID
    registry = provider_producers.iterate_providers()
    assert list(registry.ids) == sorted(
        str(entry["id"]) for entry in ITERATE_ENTRIES
    ), "the failed build leaves the product registry as it was"


def test_an_unregistered_id_refuses_and_names_what_is_registered() -> None:
    registry = provider_producers.iterate_providers()
    with pytest.raises(ProviderRegistryError) as caught:
        resolve_provider(registry, "iterate-engine")
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_UNKNOWN
    for provider_id in registry.ids:
        assert provider_id in error.remedy


def test_an_undeclared_method_refuses_and_names_what_the_provider_offers() -> None:
    registry = provider_producers.iterate_providers()
    with pytest.raises(ProviderRegistryError) as caught:
        require_method(registry, provider_producers.AE_PROVIDER_ID, "render-markdown")
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_METHOD
    assert provider_producers.AE_METHOD_NAME in error.remedy
    assert provider_producers.EVIDENCE_PACK_TOKEN in error.remedy


def test_a_foreign_declaration_the_contract_cannot_read_is_a_refusal_not_a_drop() -> None:
    with pytest.raises(ProviderRegistryError):
        provider_producers.ids_in_common_with(
            [{**ITERATE_ENTRIES[0], "schemaVersion": "iterate.provider/0.9"}]
        )


# ------------------------------------------------------------------------- ids in common (rule 6) ---


def test_the_shipped_registries_are_provably_collision_free() -> None:
    """The other harness's own block shares no id with ours -- the fixture's ``idsInCommon``."""
    assert provider_producers.ids_in_common_with(GLASSPANE_ENTRIES) == REGISTRY_BLOCK["idsInCommon"]


def test_ids_in_common_with_names_exactly_the_colliding_ids_sorted() -> None:
    """Re-registering our ids from the other side is the collision the contract reports.

    The fixture's iterate block is the same two ids under a different version, which is the
    shape rule (6) refuses to settle at run time: identical names, no way to tell which
    declaration the run consulted.
    """
    common = provider_producers.ids_in_common_with(ITERATE_ENTRIES)
    assert common == sorted(str(entry["id"]) for entry in ITERATE_ENTRIES)
    assert common == sorted(common)


def test_a_single_foreign_claim_of_one_of_our_ids_is_the_only_collision() -> None:
    claim = [{**COLLISION["a"][0], "id": provider_producers.AE_PROVIDER_ID}]
    assert provider_producers.ids_in_common_with(claim) == [provider_producers.AE_PROVIDER_ID]


def test_the_collision_block_is_reported_by_the_same_builder_we_use_for_ours() -> None:
    """The negative control the corpus supplies: the same id, two different judgements.

    Built through this product's own builder, because the point is that the builder is the only
    way in: the two sides share exactly the ids the file expects, and each still answers with
    its own prerequisites -- which is why the answer is a rename or one registry, never
    "whichever map the run consulted first".
    """
    a = provider_producers.build_registry(COLLISION["a"])
    b = provider_producers.build_registry(COLLISION["b"])
    shared = provider_registry.provider_ids_in_common(a, b)
    assert shared == [str(entry) for entry in COLLISION["expect"]]
    assert shared == sorted(shared)

    diverging = sorted(
        provider_id
        for provider_id in shared
        if resolve_provider(a, provider_id).to_json()["methods"]
        != resolve_provider(b, provider_id).to_json()["methods"]
    )
    assert diverging, "a shared id whose declarations agree is not a collision"
    note = str(COLLISION["note"])
    assert "glasspane-ae" in note, "the note names the id whose two meanings it is warning about"
    assert diverging == ["glasspane-ae"]


# --------------------------------------------------------------------------------- ae_method_for ---


def _real_pack() -> dict[str, Any]:
    corpus: dict[str, Any] = json.loads(
        (CORPUS / "evidence-decision.ok-01.json").read_text(encoding="utf-8")
    )
    for case in corpus["cases"]:
        candidate = case.get("input")
        if isinstance(candidate, dict) and isinstance(candidate.get("operationId"), str):
            return candidate
    raise AssertionError("the corpus carries no real evidence pack")


def test_a_real_pack_is_priced_by_the_registered_diagnose_method() -> None:
    assert provider_producers.ae_method_for(_real_pack()) == provider_producers.AE_METHOD_NAME


def test_a_pack_of_empty_signals_refuses_through_the_contract_naming_what_is_offered() -> None:
    """The rule this exists for: an unbacked pack is a refusal, not a fall-through.

    A method the provider never declared is what gets asked, so ``require_method`` answers with
    its code and remedy -- and the remedy names ``diagnose`` with the prerequisite it is
    offered under. Falling through to a local reading would turn 'nothing was measured' into a
    verdict on the app.
    """
    hollow = {
        "schemaVersion": "glasspane.evidence/0.1",
        "operationId": "op_hollow",
        "attribution": {},
        "circuitBreaker": {},
        "signals": {},
    }
    with pytest.raises(ProviderRegistryError) as caught:
        provider_producers.ae_method_for(hollow)
    error: ProviderRegistryError = caught.value
    assert error.code == PROVIDER_E_METHOD
    assert provider_producers.AE_PROVIDER_ID in error.message
    assert provider_producers.AE_METHOD_NAME in error.remedy
    assert provider_producers.EVIDENCE_PACK_TOKEN in error.remedy


def test_signals_carried_distinguishes_absent_from_empty() -> None:
    """Rule (3) applied to material: a null signal states nothing, an empty log states nothing."""
    assert provider_producers.signals_carried({"handlerProbe": None, "stateDiff": {}}) == frozenset()
    carried = provider_producers.signals_carried({"signals": {"pixelDiff": {"changedPixelRatio": 0}}})
    assert "pixel-diff" in carried
    assert provider_producers.EVIDENCE_PACK_TOKEN in carried, "a pack holding signals holds the pack"
    assert provider_producers.signals_carried([]) == frozenset()
    assert provider_producers.DECISION_LOG_TOKEN in provider_producers.signals_carried(
        [make_entry(entry_type="report", round_number=1, data={"verdict": "converged"})]
    )


# ------------------------------------------------------------------------------ consumer: the relay ---


def test_the_relay_relays_a_real_pack_under_the_registered_method() -> None:
    pack = _real_pack()
    answer = relay_answer(
        "gp_last_evidence", {"operationId": pack["operationId"]}, {"evidencePack": pack}
    )
    assert answer.state == "answered"
    assert answer.method == provider_producers.AE_METHOD_NAME
    run = ReverificationRun(request_digest="d", attempted=True, answers=(answer,))
    lines = " ".join(run.to_model_lines())
    assert f"via {provider_producers.AE_METHOD_NAME}" in lines, (
        "the answer has to say which registration licensed the reading"
    )


def test_the_relay_refuses_a_pack_the_provider_will_not_price() -> None:
    hollow = {
        "schemaVersion": "glasspane.evidence/0.1",
        "operationId": "op_hollow",
        "attribution": {},
        "circuitBreaker": {},
        "signals": {},
    }
    answer = relay_answer("gp_last_evidence", {}, {"evidencePack": hollow})
    assert answer.state == "refused"
    assert answer.code == PROVIDER_E_METHOD
    assert answer.outcome is None, "a refusal must not carry a verdict"
    assert answer.method is None
    assert answer.remedy
    run = ReverificationRun(request_digest="d", attempted=True, answers=(answer,))
    assert not run.all_clean, "a provider refusal means the re-verified claim is not clean"
    lines = " ".join(run.to_model_lines())
    assert "engine outcome" not in lines and PROVIDER_E_METHOD in lines


# --------------------------------------------------------------------------- consumer: the render ---


def test_the_renderer_renders_when_the_log_it_was_given_holds_entries() -> None:
    entries = [make_entry(entry_type="report", round_number=1, data={"verdict": "converged"})]
    body = render_markdown(
        ReportSummary(verdict="converged", total_findings=0, findings=[], mode="dry-run"),
        decision_log=entries,
    )
    assert "iterate report" in body


def test_the_renderer_refuses_a_run_that_recorded_nothing() -> None:
    """Reachable in CI: ``--pr --fail-on none`` with an empty decision log.

    Before this wiring it posted 'No findings recorded.' -- a clean-looking comment for a run
    that measured nothing. The registered renderer declares ``requires: ["decision-log"]``, so
    the refusal is what gets published instead.
    """
    body = render_markdown(ReportSummary(findings=[]), decision_log=[])
    assert PROVIDER_E_METHOD in body
    assert provider_producers.RENDER_METHOD_NAME in body
    assert "No findings recorded." not in body


def test_a_caller_that_said_nothing_about_its_material_is_not_claiming_an_empty_log() -> None:
    """Absent and empty are different claims; the renderer may not confuse them either."""
    assert "No findings recorded." in render_markdown(ReportSummary(findings=[]))


def test_the_cli_pr_path_hands_the_renderer_its_own_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reachability proof: the shipped command, an empty decision log, a faked poster.

    ``ih iterate report --pr --fail-on none`` on a repo with no log used to publish "No
    findings recorded." -- a clean-looking comment for a run that measured nothing. It now
    publishes the provider's refusal, because the CLI hands ``render_markdown`` the log it
    rendered from and the registration says what that method needs.
    """
    from typer.testing import CliRunner

    from iterate_harness import cli

    monkeypatch.chdir(tmp_path)
    captured: dict[str, object] = {}

    def fake_post(body: str, cwd: str, **kwargs: object) -> PostResult:
        captured["body"] = body
        return PostResult("updated", "#1")

    monkeypatch.setattr(pr_comment, "post_pr_comment", fake_post)
    result = CliRunner().invoke(cli.app, ["iterate", "report", "--pr", "--fail-on", "none"])
    assert result.exit_code == 0, result.output
    body = str(captured["body"])
    assert PROVIDER_E_METHOD in body
    assert "No findings recorded." not in body


# ------------------------------------------------------------------------- the corpus link ---


def test_this_module_is_the_consumer_the_corpus_pin_names() -> None:
    """No dead contract data: the manifest must name the product that reads the fixture.

    Asserted rather than trusted -- :mod:`.provider_producers` is the half that registers, and a
    pin whose ``consumedBy`` list no longer includes it is a pin nobody consumes.
    """
    manifest: dict[str, Any] = json.loads(
        (CORPUS / "manifest.json").read_text(encoding="utf-8")
    )
    entry = next(
        item
        for item in manifest["files"]
        if str(item.get("path", "")) == "provider-registration.ok-01.json"
    )
    consumers = [str(path) for path in entry["consumedBy"]]
    assert "src/iterate_harness/iterate/provider_producers.py" in consumers
    for consumer in consumers:
        assert (CORPUS.parents[1] / consumer).is_file(), f"{consumer} does not exist"


def test_the_public_names_resolve_through_the_lazy_export_map() -> None:
    """``iterate/__init__.py`` re-exports lazily; an unregistered name would be invisible."""
    from iterate_harness import iterate as iterate_pkg

    for name in (
        "ae_method_for",
        "build_registry",
        "ids_in_common_with",
        "iterate_providers",
        "producer_payloads",
        "render_method_for",
        "signals_carried",
    ):
        assert getattr(iterate_pkg, name) is getattr(provider_producers, name)
        assert name in iterate_pkg.__all__
