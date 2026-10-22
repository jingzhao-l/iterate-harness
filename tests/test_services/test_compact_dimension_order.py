"""Scenario ②'s consuming half: microcompact orders its clearing by the kernel's verdict.

A compaction seam that clears the oldest tool results first is a seam that throws away the
evidence of the dimension still under review, because that is the one whose results are
recent. The ledger now says which decision served which dimension, ``dimension_context`` says
which dimensions are verified, and the two together let the seam keep the *shape* of its
budget while choosing better: verified dimensions' history goes first, an open dimension's
history stays, and a block nobody attributed is left exactly where the age order put it.

Every test here compares against what the seam does today rather than against a hand-written
list, because "unchanged except where the verdict speaks" is the claim under test.
"""

from __future__ import annotations

from pathlib import Path

from iterate_harness.engine.messages import ConversationMessage, ToolResultBlock, ToolUseBlock
from iterate_harness.iterate import decision_log, dimension_ledger
from iterate_harness.iterate.dimension_context import (
    dimension_context,
    format_dimension_context,
)
from iterate_harness.services.compact import (
    TIME_BASED_MC_CLEARED_MESSAGE,
    _collect_compactable_tool_ids,
    microcompact_messages,
)

# Operation ids: ``op_`` plus 26 characters of Crockford base32, built so that a change to
# the imported pattern breaks these fixtures loudly instead of silently unattributing them.
OP_SECURITY = "op_" + "A" * 26
OP_PERFORMANCE = "op_" + "B" * 26
OP_SHARED = "op_" + "C" * 26

TWO_DIMENSIONS = "dimensions:\n  - security\n  - performance\n"


def write_config(tmp_path: Path, content: str) -> None:
    (tmp_path / "iterate.config.yaml").write_text(content, encoding="utf-8")


def record(tmp_path: Path, dimension: str, operation_id: str | None = None) -> None:
    """One attributed decision, written the way the production writer writes it."""
    data = {"dimension": dimension, "kind": "review-note"}
    if operation_id is not None:
        data["operationId"] = operation_id
    decision_log.append_entry(
        tmp_path,
        decision_log.make_entry(entry_type="decision", round_number=1, data=data),
    )


def conversation(tool_ids_and_results: list[tuple[str, str]]) -> list[ConversationMessage]:
    """assistant tool_use / user tool_result pairs, in the order they happened."""
    messages: list[ConversationMessage] = []
    for tool_id, result_text in tool_ids_and_results:
        messages.append(
            ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id=tool_id, name="read_file", input={"path": "src/a.py"})],
            )
        )
        messages.append(
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id=tool_id, content=result_text)],
            )
        )
    return messages


def cleared_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    """The results microcompact actually replaced, in conversation order."""
    return [
        block.tool_use_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.content == TIME_BASED_MC_CLEARED_MESSAGE
    ]


def surviving_tool_ids(messages: list[ConversationMessage]) -> list[str]:
    return [
        block.tool_use_id
        for message in messages
        for block in message.content
        if isinstance(block, ToolResultBlock) and block.content != TIME_BASED_MC_CLEARED_MESSAGE
    ]


class TestOrderingByKernelVerdict:
    def test_a_verified_dimensions_results_are_cleared_before_an_open_ones(self, tmp_path):
        """The scenario itself: compress the finished dimensions' history, spare the live one.

        ``security`` has one recorded decision, which is all the kernel's rule 1 needs to
        call it verified -- and that judgement, not a count compared here, is what the
        ordering is allowed to act on. The other block names a dimension the plan never
        declared: rule 1 means a *planned* dimension with a recorded decision is verified, so
        an unplanned id is the only attribution that can legitimately mean "not finished".
        """
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "ghost", OP_PERFORMANCE)  # never planned: not a verified fact
        record(tmp_path, "security", OP_SECURITY)

        messages = conversation(
            [
                ("old-open", f"grep output naming {OP_PERFORMANCE}"),
                ("middle", "plain output, nobody attributed this"),
                ("new-verified", f"review notes for {OP_SECURITY}"),
            ]
        )
        age_order = _collect_compactable_tool_ids(messages)
        verdict_order = _collect_compactable_tool_ids(messages, project_root=tmp_path)

        assert age_order == ["old-open", "middle", "new-verified"]
        assert verdict_order == ["new-verified", "middle", "old-open"], (
            "the verified dimension's result moves to the front (cleared first), the open "
            "dimension's goes to the back, and the unattributed block stays in its own slot"
        )

        compacted, _saved = microcompact_messages(messages, keep_recent=1, project_root=tmp_path)
        assert set(cleared_tool_ids(compacted)) == {"new-verified", "middle"}
        assert surviving_tool_ids(compacted) == ["old-open"], (
            "the dimension still under review keeps its evidence"
        )

    def test_an_unattributed_conversation_is_ordered_exactly_as_the_age_order_it_used(
        self, tmp_path
    ):
        """The control: nothing attributed means nothing moved -- asserted against the seam's own output."""
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "security")  # a decision with no operation id attributes no result
        messages = conversation(
            [(f"t{i}", f"output {i} " + "x" * 4000) for i in range(6)]
        )
        assert _collect_compactable_tool_ids(messages, project_root=tmp_path) == (
            _collect_compactable_tool_ids(messages)
        )

        plain, _ = microcompact_messages(conversation(
            [(f"t{i}", f"output {i} " + "x" * 4000) for i in range(6)]
        ))
        rooted, _ = microcompact_messages(conversation(
            [(f"t{i}", f"output {i} " + "x" * 4000) for i in range(6)]
        ), project_root=tmp_path)
        assert cleared_tool_ids(plain) == cleared_tool_ids(rooted)

    def test_unattributed_blocks_keep_their_indices_while_attributed_ones_swap_around_them(
        self, tmp_path
    ):
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "security", OP_SECURITY)
        record(tmp_path, "ghost", OP_PERFORMANCE)  # unplanned id: attributed, never verified
        messages = conversation(
            [
                ("a", "unattributed one"),
                ("b", f"names {OP_PERFORMANCE}"),
                ("c", "unattributed two"),
                ("d", f"names {OP_SECURITY}"),
                ("e", "unattributed three"),
            ]
        )
        ordered = _collect_compactable_tool_ids(messages, project_root=tmp_path)
        assert ordered == ["a", "d", "c", "b", "e"]
        for tool_id, index in (("a", 0), ("c", 2), ("e", 4)):
            assert ordered[index] == tool_id, (
                "an unattributed block holds the position the age order gave it"
            )

    def test_an_older_open_dimension_survives_a_newer_verified_one(self, tmp_path):
        """Mixed: the age order would have cleared the open one; the verdict says otherwise.

        Budget of one kept slot: age order keeps the newest block, which here is the
        verified dimension's, and throws the open dimension's oldest result away. Ordered by
        the verdict, the same budget keeps the open result and clears the verified one --
        the count never changes, only which one.
        """
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "security", OP_SECURITY)
        record(tmp_path, "ghost", OP_PERFORMANCE)
        payloads = [
            ("open-oldest", f"oldest result, from an undeclared dimension {OP_PERFORMANCE}"),
            ("plain", "unattributed, and the age order would have cleared it"),
            ("verified-newest", f"newest result, dimension verified {OP_SECURITY}"),
        ]
        by_age, _ = microcompact_messages(conversation(payloads), keep_recent=1)
        assert cleared_tool_ids(by_age) == ["open-oldest", "plain"], (
            "what the seam does today: the open dimension's history is the first thing it loses"
        )

        compacted, _saved = microcompact_messages(
            conversation(payloads), keep_recent=1, project_root=tmp_path
        )
        assert set(cleared_tool_ids(compacted)) == {"verified-newest", "plain"}
        assert surviving_tool_ids(compacted) == ["open-oldest"]


class TestDeterministicWinner:
    def test_an_operation_named_by_two_decisions_uses_the_last_attribution_in_the_ledger(
        self, tmp_path
    ):
        """Same op id, two dimensions: the newest ledger attribution is the run's answer.

        The rule is stated in ``dimension_ledger.operation_dimension_map``; here it is the
        ordering that must not waver -- a tie broken by dict order would give two compactions
        of the same conversation different budgets.
        """
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "ghost", OP_SHARED)  # first: an id the plan never declared
        record(tmp_path, "security", OP_SHARED)  # last: the attribution that wins
        record(tmp_path, "security", OP_PERFORMANCE)

        messages = conversation(
            [
                ("shared", f"a result naming {OP_SHARED}"),
                ("other", f"a result naming {OP_PERFORMANCE}"),
                ("plain", "unattributed"),
            ]
        )
        assert dimension_ledger.operation_dimension_map(tmp_path) == {
            OP_SHARED: "security",
            OP_PERFORMANCE: "security",
        }
        assert _collect_compactable_tool_ids(messages, project_root=tmp_path) == [
            "shared",
            "other",
            "plain",
        ], "with the winner resolved, both attributed blocks are verified-dimension history"

        # The same conversation over a ledger that resolved the tie the other way must push
        # the shared block back: the winner rule is what makes the order reproducible.
        reopened = tmp_path / "reopened"
        reopened.mkdir()
        write_config(reopened, TWO_DIMENSIONS)
        record(reopened, "security", OP_SHARED)
        record(reopened, "ghost", OP_SHARED)
        record(reopened, "security", OP_PERFORMANCE)
        assert _collect_compactable_tool_ids(messages, project_root=reopened) == [
            "other",
            "shared",
            "plain",
        ], (
            "with the tie resolved to an undeclared dimension, the verified one takes the "
            "earlier slot and the shared block is pushed back -- same three blocks, same "
            "budget, and the resolution rule is the only reason the order is reproducible"
        )


class TestTheSeamIsReachedFromTheCompactionCallers:
    async def test_compact_conversation_microcompacts_by_the_verdict_it_was_given(
        self, tmp_path
    ):
        """End to end: the project root handed to the compaction entry point reaches microcompact.

        Six candidates and the default budget of five kept means exactly one result is
        cleared -- so this test is really asking *which* one, and the answer must change when
        the ledger speaks. Nothing here patches the seam: the stub client only stands in for
        the summary call that comes after.
        """

        class _StubClient:
            def __init__(self) -> None:
                self.requests: list[object] = []

            async def stream_message(self, request):
                self.requests.append(request)
                from iterate_harness.api.client import ApiMessageCompleteEvent
                from iterate_harness.api.usage import UsageSnapshot
                from iterate_harness.engine.messages import ConversationMessage as _M
                from iterate_harness.engine.messages import TextBlock as _T

                yield ApiMessageCompleteEvent(
                    message=_M(role="assistant", content=[_T(text="<summary>done</summary>")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason=None,
                )

        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "security", OP_SECURITY)
        record(tmp_path, "ghost", OP_PERFORMANCE)
        payloads = [
            ("open-oldest", f"oldest result names the undeclared dimension {OP_PERFORMANCE}"),
            ("p2", "plain two"),
            ("p3", "plain three"),
            ("p4", "plain four"),
            ("p5", "plain five"),
            ("verified-newest", f"newest result names the verified dimension {OP_SECURITY}"),
        ]

        by_age = conversation(payloads)
        from iterate_harness.services.compact import compact_conversation

        await compact_conversation(
            by_age, api_client=_StubClient(), model="claude-test", preserve_recent=1
        )
        assert cleared_tool_ids(by_age) == ["open-oldest"], (
            "the age order spends its one cleared slot on the dimension still under review"
        )

        by_verdict = conversation(payloads)
        await compact_conversation(
            by_verdict,
            api_client=_StubClient(),
            model="claude-test",
            preserve_recent=1,
            project_root=tmp_path,
        )
        assert cleared_tool_ids(by_verdict) == ["verified-newest"]


class TestBudgetIsUntouched:
    def test_the_number_of_cleared_blocks_is_the_number_the_budget_ever_bought(self, tmp_path):
        """Only WHICH results go changes -- never how many, and never one that survives today."""
        write_config(tmp_path, TWO_DIMENSIONS)
        record(tmp_path, "security", OP_SECURITY)
        record(tmp_path, "ghost", OP_PERFORMANCE)
        payloads = [
            ("p1", f"names {OP_PERFORMANCE} " + "x" * 300),
            ("p2", "plain " + "x" * 300),
            ("p3", f"names {OP_SECURITY} " + "x" * 300),
            ("p4", "plain " + "x" * 300),
            ("p5", f"names {OP_SECURITY} " + "x" * 300),
            ("p6", "plain " + "x" * 300),
        ]
        for keep in (1, 2, 3, 5, 8):
            plain, _ = microcompact_messages(conversation(payloads), keep_recent=keep)
            rooted, _ = microcompact_messages(
                conversation(payloads), keep_recent=keep, project_root=tmp_path
            )
            assert len(cleared_tool_ids(plain)) == len(cleared_tool_ids(rooted)), (
                f"keep_recent={keep} must clear the same count it always cleared"
            )
            assert len(cleared_tool_ids(plain)) == max(0, len(payloads) - keep)
            assert set(surviving_tool_ids(plain)) | set(cleared_tool_ids(plain)) == {
                tool_id for tool_id, _ in payloads
            }

    def test_a_project_with_no_ledger_at_all_falls_back_to_the_age_order(self, tmp_path):
        """No recorded decision: the verdict is 0/N verified and the seam moves nothing."""
        write_config(tmp_path, TWO_DIMENSIONS)
        messages = conversation(
            [("m1", "first " + "y" * 500), ("m2", f"names an id nobody recorded {OP_SECURITY}")]
        )
        assert _collect_compactable_tool_ids(messages, project_root=tmp_path) == (
            _collect_compactable_tool_ids(messages)
        )
        assert format_dimension_context(
            dimension_context(
                ["security", "performance"],
                {dimension: {"decisions": n} for dimension, n in
                 dimension_ledger.recorded_dimension_counts(tmp_path).items()},
            )
        ).startswith("0/2 dimensions verified"), (
            "nothing recorded is the kernel's 0/N fact, never an invented verdict"
        )

    def test_a_project_root_that_cannot_be_read_leaves_the_seam_exactly_as_it_was(self, tmp_path):
        """An unreadable attribution must not become a reason to clear more, or fewer."""
        messages = conversation([(f"r{i}", f"result {i} " + "z" * 500) for i in range(4)])
        expected = _collect_compactable_tool_ids(messages)
        assert _collect_compactable_tool_ids(messages, project_root=tmp_path / "nowhere") == expected
