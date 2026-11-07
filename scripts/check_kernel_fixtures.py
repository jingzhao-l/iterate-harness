#!/usr/bin/env python3
"""Check the vendored kernel contract corpus against the contract's real source.

``tests/kernel_fixtures/`` holds a copy of the fixtures published inside the
``iterate-kernel`` npm package. A copy is only a contract while somebody compares
it, so this script answers one question in two modes:

* default — fetch ``iterate-kernel@<pinned version>`` from the registry and check
  that every pinned file hashes the same there. This catches a stale pin: the
  kernel republished the contract and this repo is still asserting old bytes.
* ``--source DIR`` — check the pin against a kernel ``fixtures`` directory in a
  checkout. This catches the opposite direction, and needs no network: the kernel
  moved a fixture and the corpus beside it was not re-pinned. The iterate-skill
  monorepo CI runs exactly this, because both directories are in that tree.

Either mismatch exits non-zero. Silence is not a pass: a pinned file that cannot
be fetched, unpacked, or found is a failure, not a skip.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "tests" / "kernel_fixtures"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"


class PinNotPublished(Exception):
    """The pinned kernel version is not on the registry (not released yet)."""


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version_key(value: str) -> tuple[int, ...]:
    """Parse a dotted version into a sortable tuple (``0.1.11`` -> ``(0, 1, 11)``)."""
    parts: list[int] = []
    for segment in value.strip().split("."):
        digits = "".join(ch for ch in segment if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _published_latest(package: str) -> str | None:
    """Return the registry ``latest`` version for ``package``, or ``None``."""
    try:
        result = subprocess.run(
            ["npm", "view", package, "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def load_manifest(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise SystemExit(f"manifest at {path} is not a JSON object")
    return loaded


def fetch_published(package: str, version: str, destination: Path) -> Path:
    """Download the published tarball for ``package@version`` (no install)."""
    # npm pack writes into an existing directory only; a missing one surfaces as an
    # ENOENT on the tarball path, which reads like "the package is not published".
    destination.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["npm", "pack", f"{package}@{version}", "--pack-destination", str(destination)],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise SystemExit("npm is not on PATH — cannot verify the pin against the registry")
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.strip() or error.stdout.strip()
        if "notarget" in stderr or "No matching version" in stderr:
            # A pin to a version the registry does not carry is not necessarily a
            # stale pin: the kernel may simply not have published this version
            # yet (the standalone repo has no kernel/ checkout to check against).
            # main() decides between "ahead of published" (warn) and a broken
            # pin (fail) — see the PinNotPublished handler there.
            raise PinNotPublished(f"{package}@{version} is not published") from error
        raise SystemExit(f"npm pack {package}@{version} failed: {stderr}")
    # npm prints the path it wrote; fall back to a glob so a changed npm still works.
    printed = result.stdout.strip().splitlines()
    if printed:
        candidate = Path(printed[-1])
        if candidate.is_file():
            return candidate
    tarballs = sorted(destination.glob(f"{package}-*.tgz"))
    if not tarballs:
        raise SystemExit(f"npm pack produced no tarball for {package}@{version}")
    return tarballs[-1]


def unpack_fixtures(tarball: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as archive:
        # The "data" filter refuses members that would escape the destination
        # (absolute paths, ../ segments). Python before 3.11.4 has no filter
        # argument, and this tarball comes from the registry — untrusted by default.
        if hasattr(tarfile, "data_filter"):
            archive.extractall(destination, filter="data")
        else:
            archive.extractall(destination)
    package_dir = destination / "package"
    if not package_dir.is_dir():
        raise SystemExit(f"{tarball} has no package/ root — unexpected artifact layout")
    fixtures = package_dir / "fixtures"
    if not fixtures.is_dir():
        raise SystemExit(
            f"{package_dir.name} does not ship fixtures/ — the published artifact "
            "cannot anchor a cross-language contract. Bump the kernel and republish."
        )
    return fixtures


def resolve_source(args: argparse.Namespace, manifest: dict[str, Any]) -> tuple[Path, tempfile.TemporaryDirectory[str], str]:
    """Return the directory the pin must match, plus the cleanup handle."""
    cleanup = tempfile.TemporaryDirectory()
    if args.source:
        source = Path(args.source)
        if not source.is_dir():
            raise SystemExit(f"--source {source} is not a directory")
        label = f"checkout {source}"
        return source, cleanup, label
    package = str(manifest.get("package", ""))
    version = str(manifest.get("version", ""))
    if not package or not version:
        raise SystemExit("manifest must pin both a package name and an exact version")
    tarball = fetch_published(package, version, Path(cleanup.name) / "dl")
    fixtures = unpack_fixtures(tarball, Path(cleanup.name) / "unpacked")
    return fixtures, cleanup, f"published {package}@{version}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--source",
        help="a kernel fixtures/ directory to check the pin against, instead of the registry",
    )
    args = parser.parse_args(argv)

    manifest = load_manifest(MANIFEST_PATH)
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise SystemExit("manifest pins no files")

    try:
        source, cleanup, label = resolve_source(args, manifest)
    except PinNotPublished as exc:
        if args.source:
            raise
        package = str(manifest.get("package", ""))
        version = str(manifest.get("version", ""))
        latest = _published_latest(package)
        if latest is not None and _version_key(version) > _version_key(latest):
            # The pin is ahead of every published version: the kernel has not
            # released this contract yet, so there is nothing on the registry to
            # be *stale* against. The authoritative check runs in the monorepo CI
            # (``--source kernel/fixtures``, where the kernel checkout lives);
            # the standalone repo must not go red waiting for an upstream publish
            # it does not control. Keep the stale-pin failure below.
            print(
                f"WARN {exc}: pin is ahead of the published latest ({latest}); the "
                "monorepo CI verifies the same pin against kernel/fixtures (the "
                "source of truth). Registry verification activates once the kernel "
                "publishes this version.",
                file=sys.stderr,
            )
            return 0
        raise SystemExit(
            f"{exc}: cannot verify a pin to an unpublished version (latest on the "
            f"registry is {latest or 'unknown'}) — this is a broken pin, not a "
            "stale one."
        )

    with cleanup:
        failures: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                failures.append(f"malformed manifest entry: {entry!r}")
                continue
            name = str(entry.get("path", ""))
            pinned_hash = str(entry.get("sha256", ""))
            vendored = CORPUS_DIR / name
            source_file = source / name
            if not vendored.is_file():
                failures.append(f"{name}: pinned here but the corpus file is missing")
                continue
            if sha256_of(vendored) != pinned_hash:
                failures.append(
                    f"{name}: the corpus file ({sha256_of(vendored)}) does not match its "
                    f"own manifest pin ({pinned_hash}) — the copy was edited in place"
                )
            if not source_file.is_file():
                failures.append(f"{name}: not present in {label}")
                continue
            actual = sha256_of(source_file)
            if actual != pinned_hash:
                failures.append(
                    f"{name}: {label} carries {actual}, this repo pins {pinned_hash} — "
                    "the contract moved. Re-copy the fixture, re-pin the manifest, and "
                    "make the consumer test pass (or treat the kernel change as the bug)."
                )

        if failures:
            for failure in failures:
                print(f"FAIL {failure}", file=sys.stderr)
            print(f"\n{len(failures)} fixture problem(s).", file=sys.stderr)
            return 1

    print(f"kernel fixtures OK: {len(entries)} file(s) match the pin and {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
