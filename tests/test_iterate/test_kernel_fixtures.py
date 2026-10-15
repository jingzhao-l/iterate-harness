"""The vendored kernel contract corpus is pinned, complete, and load-bearing.

Why this file exists: the kernel is TypeScript and this harness is Python, so the
only thing the two can share without a build step is the fixture corpus published
with ``iterate-kernel``. Copying it is not enough — a copy that is never compared
against the source becomes a second truth, which is exactly how "both sides agree"
quietly stops meaning anything. Three properties are enforced here:

1. **Bytes == pin.** Each file's sha256 must equal the manifest. Editing a
   vendored fixture to make a test pass turns red instead of rewriting history.
2. **No dead contract data.** Every entry names consumer paths that exist, and
   every file in the corpus is in the manifest. An orphan fixture is a contract
   nobody asserts.
3. **The pin can be checked against the live source.** When
   ``KERNEL_FIXTURES_DIR`` points at a kernel checkout (the kernel repo's CI does
   this), its bytes must match the pin too — so moving the contract without
   re-pinning here fails in the *other* repo, which is the drift warning this
   whole arrangement is for.

The last test is a negative control: it tampers with a copy and proves the
checker rejects it. A gate that has never been observed failing is not a gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

#: The corpus lives next to the tests that consume it, not in the package: it is
#: test data, and shipping it inside ``iterate_harness`` would make the published
#: wheel carry a copy of another project's contract.
CORPUS_DIR = Path(__file__).resolve().parents[1] / "kernel_fixtures"
MANIFEST_NAME = "manifest.json"

#: An exact version, never a range: a range would let the contract move under the
#: pin and still report green.
_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), f"{manifest_path} is not a JSON object"
    return loaded


def verify_corpus(corpus_dir: Path, manifest: dict[str, Any]) -> list[str]:
    """Return every violation found in ``corpus_dir`` against ``manifest``.

    Empty list means the corpus is exactly what the pin declares. Kept free of
    assertions so the negative control can run it on tampered data.
    """
    violations: list[str] = []

    version = manifest.get("version")
    if not isinstance(version, str) or not _EXACT_VERSION.match(version):
        violations.append(f"manifest version must be an exact semver, got {version!r}")

    package = manifest.get("package")
    if not isinstance(package, str) or not package:
        violations.append("manifest must name the package the corpus came from")

    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        return violations + ["manifest lists no files"]

    pinned: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            violations.append(f"malformed manifest entry: {entry!r}")
            continue
        name = str(entry["path"])
        if name in pinned:
            violations.append(f"duplicate manifest entry for {name}")
        pinned[name] = entry

    present = {p.name for p in corpus_dir.iterdir() if p.is_file() and p.name != MANIFEST_NAME}
    for orphan in sorted(present - set(pinned)):
        violations.append(f"{orphan} is in the corpus but not pinned by the manifest")
    for missing in sorted(set(pinned) - present):
        violations.append(f"{missing} is pinned by the manifest but absent from the corpus")

    for name, entry in pinned.items():
        path = corpus_dir / name
        if not path.is_file():
            continue
        expected_hash = entry.get("sha256")
        actual_hash = _sha256(path)
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            violations.append(f"{name} sha256 {actual_hash} != pinned {expected_hash!r}")
        expected_bytes = entry.get("bytes")
        if isinstance(expected_bytes, int) and path.stat().st_size != expected_bytes:
            violations.append(f"{name} is {path.stat().st_size} bytes, pinned {expected_bytes}")

        consumers = entry.get("consumedBy")
        if not isinstance(consumers, list) or not consumers:
            violations.append(f"{name} declares no consumer — it is dead contract data")
            continue
        repo_root = corpus_dir.parents[1]
        for consumer in consumers:
            if not isinstance(consumer, str) or not (repo_root / consumer).is_file():
                violations.append(f"{name} names consumer {consumer!r}, which does not exist")

    return violations


def test_the_vendored_corpus_matches_its_pin() -> None:
    """The corpus in this repo is exactly the bytes the manifest declares."""
    manifest = _load_manifest(CORPUS_DIR / MANIFEST_NAME)
    assert verify_corpus(CORPUS_DIR, manifest) == []


def test_the_live_kernel_checkout_agrees_with_the_pin() -> None:
    """When a kernel checkout is available, its bytes must equal the pin.

    This is the drift alarm: the kernel changes a fixture, this goes red, and the
    only legal fixes are to re-pin here *and* make the consumer test pass, or to
    revert the kernel. Silence here means the two implementations are still
    asserted against the same bytes.
    """
    override = os.environ.get("KERNEL_FIXTURES_DIR")
    if not override:
        # The corpus in this repo is checked either way, so a machine without a
        # kernel checkout still runs a real comparison above. An explicit override
        # that points nowhere, however, is a misconfiguration and must not pass.
        return
    source = Path(override)
    assert source.is_dir(), f"KERNEL_FIXTURES_DIR={override} is not a directory"
    manifest = _load_manifest(CORPUS_DIR / MANIFEST_NAME)
    entries = manifest.get("files", [])
    assert isinstance(entries, list)
    for entry in entries:
        assert isinstance(entry, dict)
        name = entry.get("path")
        assert isinstance(name, str)
        live = source / name
        assert live.is_file(), f"{name} is not in the live kernel checkout at {source}"
        assert _sha256(live) == entry.get("sha256"), (
            f"{name} moved in the kernel without being re-pinned here: the two "
            "implementations are no longer asserted against the same bytes"
        )


def test_a_tampered_fixture_is_rejected() -> None:
    """Negative control: the checker has teeth.

    Without this, "the corpus matches its pin" would be a claim about code that
    has never refused anything.
    """
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "kernel_fixtures"
        shutil.copytree(CORPUS_DIR, scratch)
        manifest = _load_manifest(scratch / MANIFEST_NAME)
        entries = manifest["files"]
        assert isinstance(entries, list)
        first = entries[0]
        assert isinstance(first, dict)
        target = scratch / str(first["path"])

        # Flip one byte of contract data; the sha256 must stop matching.
        payload = bytearray(target.read_bytes())
        payload[-2] = (payload[-2] + 1) % 256 if payload[-2] != 0xFF else payload[-2] - 1
        target.write_bytes(bytes(payload))
        violations = verify_corpus(scratch, manifest)
        assert any("sha256" in line for line in violations), violations

        # Drop a pinned file: the corpus is incomplete, not quietly smaller.
        clean = scratch.parent / "clean"
        shutil.copytree(CORPUS_DIR, clean)
        for name in ("dimension-context.ok-01.json",):
            (clean / name).unlink()
        assert any(
            "absent from the corpus" in line for line in verify_corpus(clean, manifest)
        )


def test_an_unpinned_extra_fixture_is_rejected() -> None:
    """A fixture added to the corpus without a manifest entry is a hole."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "kernel_fixtures"
        shutil.copytree(CORPUS_DIR, scratch)
        (scratch / "sneaky.ok-01.json").write_text("{}", encoding="utf-8")
        violations = verify_corpus(scratch, _load_manifest(scratch / MANIFEST_NAME))
        assert any("sneaky.ok-01.json" in line for line in violations), violations
