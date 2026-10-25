#!/usr/bin/env python3
"""Split ``pytest --collect-only -q`` output into buckets that CI can run as separate jobs.

WHY THIS FILE EXISTS. The harness's own CI ran the whole suite in one step, and that step had been
dying the same way five times: it started, nothing further was reported, and the job was cut 45
minutes after it began with "The hosted runner lost communication with the server" — and because a
GitHub Actions job log only exists once the job has ended (measured on run 40190097615: the completed
sibling job's log fetched 31,956 bytes while the in-progress test leg's log endpoint answered HTTP
404), a step that never ends leaves *no* log anywhere, no annotation, and no artifact — an
`if: always()` upload is a step, so it needs the hanging step to finish first. Instrumenting the
monolith further could not produce an observation.

So the suite stops being one job. Each bucket is a job on its own machine, short enough to *end*, and
therefore to have a log; and if a bucket still hangs, the question shrinks from "one of 3,099 tests"
to "one of the files this bucket names".

Splitting introduces its own failure mode, which is the reason this is a script with tests rather than
a shell snippet: a split that quietly drops a group is a **green CI over a subset of the suite**. The
guard against that is here, not in prose — the collected count pytest itself prints must equal the
number of node ids parsed, and the buckets must be an exact partition of the groups.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

COLLECTED_LINE = "collected"
NODE_PREFIX = "tests/"

EXIT_USAGE = 2


@dataclass(frozen=True)
class Bucket:
    """One CI job's worth of test paths."""

    bucket_id: str
    count: int
    paths: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {"id": self.bucket_id, "count": self.count, "paths": " ".join(self.paths)}


class SplitError(Exception):
    """A refusal that must make CI red instead of running a subset."""


def node_ids(collect_output: str) -> list[str]:
    """Every collected test id in ``pytest --collect-only -q`` output.

    Line-anchored on purpose: `-q` writes one node id per line, then a blank line and the
    ``N tests collected in X.XXs`` summary. A parser that searched for ``::`` anywhere would also pick
    up the summary line, a warning, or a traceback frame, and would then report a total that matches
    nothing.
    """
    return [
        line.strip()
        for line in collect_output.splitlines()
        if line.strip().startswith(NODE_PREFIX) and "::" in line
    ]


def reported_count(collect_output: str) -> int | None:
    """The number pytest printed on its own summary line, or None when there is not one.

    This is the second implementation of the count and the only thing that can catch a *silent*
    shortfall: if pytest's output format changes, `node_ids()` can return fewer ids than exist while
    still looking self-consistent.
    """
    for line in reversed(collect_output.splitlines()):
        text = line.strip()
        if COLLECTED_LINE in text and "error" not in text:
            head = text.split(" ", 1)[0]
            if head.isdigit():
                return int(head)
    return None


def group_of(node_id: str) -> str:
    """The smallest unit CI can ask pytest to run: one test module.

    A **file** is the unit rather than a directory, for two reasons that pull against each other and
    this is the balance the suite is 3,099 tests deep. Collection is atomic per module, so nothing is
    half-collected; and the granularity is what makes the buckets even — grouping by directory put
    `tests/test_iterate` alone at 1,355 of 3,099 tests, which is not a bucket, it is the same monolith
    with a number on it. Each file keeps whatever `conftest.py` governs its path, in every bucket it
    lands in, because pytest walks the confests of the *paths it was given*.
    """
    return node_id.split("::", 1)[0]


def counts_by_group(ids: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for node_id in ids:
        counts[group_of(node_id)] += 1
    return dict(counts)


def pack(counts: dict[str, int], buckets: int) -> list[Bucket]:
    """First-fit-decreasing over test modules, so no file is ever split across buckets.

    A file stays whole because collection is atomic per module and its tests share module state;
    the ordering is deterministic (largest file first, ties by name) so two runs of this
    script over the same collection produce the same buckets.
    """
    if buckets < 1:
        raise SplitError(f"SPLIT_E_NO_BUCKETS: asked for {buckets} bucket(s)")
    if len(counts) < buckets:
        raise SplitError(
            f"SPLIT_E_TOO_MANY_BUCKETS: {len(counts)} group(s) cannot fill {buckets} "
            "bucket(s) — an empty bucket is a green job that tests nothing"
        )
    load = [0] * buckets
    members: list[list[str]] = [[] for _ in range(buckets)]
    for group in sorted(counts, key=lambda name: (-counts[name], name)):
        target = min(range(buckets), key=lambda index: (load[index], index))
        members[target].append(group)
        load[target] += counts[group]
    packed: list[Bucket] = []
    for index in range(buckets):
        if not members[index]:
            raise SplitError(f"SPLIT_E_EMPTY_BUCKET: bucket {index + 1} received no group")
        packed.append(
            Bucket(
                bucket_id=f"{index + 1:02d}",
                count=sum(counts[group] for group in members[index]),
                paths=tuple(sorted(members[index])),
            )
        )
    total = sum(bucket.count for bucket in packed)
    if total != sum(counts.values()):
        raise SplitError(
            f"SPLIT_E_NOT_A_PARTITION: buckets hold {total} test(s), groups hold "
            f"{sum(counts.values())}"
        )
    return packed


def build(collect_output: str, buckets: int, expect_total: int | None = None) -> list[Bucket]:
    """Parse, cross-check, and pack — refusing rather than emitting a partial plan."""
    ids = node_ids(collect_output)
    if not ids:
        raise SplitError(
            "SPLIT_E_NO_TESTS: no `tests/...::...` line in the collected output — the plan would be "
            "empty and every bucket would be green over nothing"
        )
    reported = reported_count(collect_output)
    if reported is None:
        raise SplitError(
            "SPLIT_E_NO_COUNT: the summary line pytest prints (`N tests collected ...`) is missing, "
            "so there is nothing to check the parsed total against"
        )
    if reported != len(ids):
        raise SplitError(
            f"SPLIT_E_COUNT_MISMATCH: pytest says {reported}, this parser found {len(ids)} node "
            "id(s) — running the plan would silently drop tests from CI"
        )
    if expect_total is not None and expect_total != len(ids):
        raise SplitError(
            f"SPLIT_E_PLAN_MISMATCH: this run collected {len(ids)} test(s), the plan said "
            f"{expect_total} — the paths handed to pytest are not the set the plan was built from"
        )
    return pack(counts_by_group(ids), buckets)


def _write_github_output(path: str | None, buckets: list[Bucket], total: int) -> None:
    if path is None:
        return
    # `GITHUB_OUTPUT` is an append-only env file; each value must be a single line, which the JSON
    # below is (no pretty-printing, no embedded newline in `paths`).
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"buckets={json.dumps([b.as_json() for b in buckets])}\n")
        handle.write(f"total={total}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--buckets", type=int, default=6, help="number of CI jobs to split into")
    parser.add_argument(
        "--github-output",
        default=None,
        help="path to $GITHUB_OUTPUT; omit to only print the plan",
    )
    parser.add_argument(
        "--collect-file",
        default=None,
        help="read `pytest --collect-only -q` output from a file instead of stdin",
    )
    parser.add_argument(
        "--expect-total",
        type=int,
        default=None,
        help="refuse unless the collection holds exactly this many tests (a CI bucket re-checks "
        "the count its plan row promised)",
    )
    args = parser.parse_args(argv)

    text = (
        sys.stdin.read()
        if args.collect_file is None
        else open(args.collect_file, encoding="utf-8").read()
    )
    try:
        buckets = build(text, args.buckets, args.expect_total)
    except SplitError as error:
        print(f"[split_suite] refusing: {error}", file=sys.stderr)
        return EXIT_USAGE

    total = sum(bucket.count for bucket in buckets)
    print(f"[split_suite] {total} test(s) in {len(buckets)} bucket(s), per bucket:")
    for bucket in buckets:
        print(f"  {bucket.bucket_id}: {bucket.count:>5}  {' '.join(bucket.paths)}")
    _write_github_output(args.github_output, buckets, total)
    print(json.dumps([bucket.as_json() for bucket in buckets]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
