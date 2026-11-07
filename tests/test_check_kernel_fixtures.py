"""Tests for scripts/check_kernel_fixtures.py (kernel-contract gate).

The gate answers "does this repo's vendored corpus still match the kernel it
pins?" in two modes: against a kernel checkout (``--source``) and against the
published registry package. This file pins the newer behaviour added for the
standalone repo: a pin that is *ahead of* the published latest (the kernel has
not released it yet) is a warning, not a red build, because the authoritative
source check runs in the monorepo; a pin to an unpublished version that is NOT
ahead (a broken pin) still fails, and a stale pin (published moved on, this
repo still asserts old bytes) still fails on the hash mismatch.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_kernel_fixtures.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_kernel_fixtures", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_kernel_fixtures"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _load_module()


def _write_manifest(corpus: Path, version: str, name: str = "sample.ok-01.json") -> dict:
    corpus.mkdir(parents=True, exist_ok=True)
    content = b'{"hello": "world"}'
    digest = hashlib.sha256(content).hexdigest()
    (corpus / name).write_bytes(content)
    manifest = {
        "package": "iterate-kernel",
        "version": version,
        "sourceRepo": "jingzhao-l/iterate-skill",
        "sourcePath": "kernel/fixtures",
        "files": [
            {"path": name, "sha256": digest, "bytes": len(content), "consumedBy": ["src/x.py"]}
        ],
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_version_key_sorts_dotted_versions(module) -> None:
    assert module._version_key("0.1.11") > module._version_key("0.1.10")
    assert module._version_key("0.1.2") < module._version_key("0.1.10")
    assert module._version_key("0.1.9") < module._version_key("0.1.10")


def test_ahead_of_published_pin_warns_not_fails(module, tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, "0.1.11")
    module.CORPUS_DIR = corpus
    module.MANIFEST_PATH = corpus / "manifest.json"

    def fake_fetch(package: str, version: str, destination: Path):
        raise module.PinNotPublished(f"{package}@{version} is not published")

    module.fetch_published = fake_fetch
    module._published_latest = lambda package: "0.1.10"
    assert module.main([]) == 0
    captured = capsys.readouterr()
    assert "ahead of the published latest (0.1.10)" in captured.err


def test_broken_pin_to_unpublished_version_still_fails(module, tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus"
    _write_manifest(corpus, "0.1.9")
    module.CORPUS_DIR = corpus
    module.MANIFEST_PATH = corpus / "manifest.json"

    def fake_fetch(package: str, version: str, destination: Path):
        raise module.PinNotPublished(f"{package}@{version} is not published")

    module.fetch_published = fake_fetch
    module._published_latest = lambda package: "0.1.10"
    with pytest.raises(SystemExit) as excinfo:
        module.main([])
    assert excinfo.value.code != 0


def test_source_mode_matches_the_pin(module, tmp_path: Path, capsys) -> None:
    corpus = tmp_path / "corpus"
    manifest = _write_manifest(corpus, "0.1.11")
    source = tmp_path / "kernel_fixtures"
    source.mkdir()
    # The source carries the same bytes as the pin.
    name = manifest["files"][0]["path"]
    (source / name).write_bytes(b'{"hello": "world"}')
    module.CORPUS_DIR = corpus
    module.MANIFEST_PATH = corpus / "manifest.json"
    assert module.main(["--source", str(source)]) == 0
    captured = capsys.readouterr()
    assert "match the pin" in captured.out
