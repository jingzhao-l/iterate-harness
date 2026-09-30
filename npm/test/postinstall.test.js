/* Tests for the npm postinstall hook (scripts/postinstall.js).
 *
 * These tests must stay hermetic: they exercise the *control flow* of the
 * postinstall hook (skip flag, delegation to bootstrap.ensureRuntime, no
 * crash on failure) and must not touch the network. The previous version ran
 * a real `pip install` from PyPI, which made `npm test` a minutes-long,
 * network-dependent operation that hung indefinitely on a slow registry.
 *
 * `test/fixtures/fake-python.js` stands in for the interpreter, and
 * `ITERATE_HARNESS_INSTALL_URL` replaces the real remote artifact with a local
 * path so no download is attempted either.
 */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const os = require("node:os");

const ROOT = path.resolve(__dirname, "..");
const POSTINSTALL = path.join(ROOT, "scripts", "postinstall.js");
const BIN_BOOTSTRAP = path.join(ROOT, "lib", "bootstrap.js");
const FAKE_PYTHON = path.join(__dirname, "fixtures", "fake-python.js");

const nodeBin = process.execPath;
// The fixture is a `#!/usr/bin/env node` script, which Windows cannot exec
// directly; those two hermetic cases are skipped there rather than silently
// falling back to a real network install.
const hermeticOnly = process.platform === "win32"
  ? "fake interpreter fixture is not executable on Windows"
  : false;

function runPostinstall(env) {
  return new Promise((resolve) => {
    execFile(nodeBin, [POSTINSTALL], { env: { ...process.env, ...env } }, (error, stdout, stderr) => {
      resolve({ code: error ? error.code ?? 1 : 0, stdout, stderr });
    });
  });
}

// A local stand-in for the release artifact. The wrapper passes it straight to
// the (fake) pip, so no download and no remote install ever happens.
function localArtifact() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-artifact-"));
  const file = path.join(dir, "iterate_harness-0.0.0-py3-none-any.whl");
  fs.writeFileSync(file, "fake wheel\n");
  return { dir, file };
}

test("postinstall respects ITERATE_HARNESS_SKIP_INSTALL=1 and exits 0", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-skip-"));
  const { code, stderr } = await runPostinstall({
    ITERATE_HARNESS_NPM_HOME: home,
    ITERATE_HARNESS_SKIP_INSTALL: "1",
  });
  assert.equal(code, 0);
  assert.match(stderr, /Skipped install during npm install/);
  assert.equal(fs.existsSync(path.join(home, "venv")), false);
  fs.rmSync(home, { recursive: true, force: true });
});

test("postinstall delegates to bootstrap ensureRuntime", { skip: hermeticOnly }, async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-boot-"));
  const artifact = localArtifact();
  const pipLog = path.join(artifact.dir, "pip.log");
  const { code, stderr } = await runPostinstall({
    ITERATE_HARNESS_NPM_HOME: home,
    ITERATE_HARNESS_SKIP_INSTALL: "",
    ITERATE_HARNESS_PYTHON: FAKE_PYTHON,
    ITERATE_HARNESS_INSTALL_URL: artifact.file,
    ITERATE_HARNESS_FAKE_PIP_LOG: pipLog,
  });
  // The hook must never crash: it either reports a completed install or
  // swallows the failure with guidance.
  assert.equal(code, 0);
  assert.match(stderr, /installed during npm install|could not install the harness/i);
  // On the hermetic path the install really does complete, so assert the
  // outcome rather than accepting either branch.
  assert.match(stderr, /installed during npm install/i);
  const pipCalls = fs.readFileSync(pipLog, "utf8");
  assert.match(pipCalls, /-m pip install/);
  // The version stamp must be written, otherwise every `ih` invocation would
  // re-run the whole bootstrap.
  assert.equal(fs.existsSync(path.join(home, "venv", "..", "version.stamp")), true);
  fs.rmSync(home, { recursive: true, force: true });
  fs.rmSync(artifact.dir, { recursive: true, force: true });
});

test("postinstall reuses an up-to-date runtime instead of reinstalling", { skip: hermeticOnly }, async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-reuse-"));
  const artifact = localArtifact();
  const pipLog = path.join(artifact.dir, "pip.log");
  const env = {
    ITERATE_HARNESS_NPM_HOME: home,
    ITERATE_HARNESS_SKIP_INSTALL: "",
    ITERATE_HARNESS_PYTHON: FAKE_PYTHON,
    ITERATE_HARNESS_INSTALL_URL: artifact.file,
    ITERATE_HARNESS_FAKE_PIP_LOG: pipLog,
  };
  const first = await runPostinstall(env);
  assert.equal(first.code, 0);
  const afterFirst = fs.readFileSync(pipLog, "utf8").split("\n").filter(Boolean).length;
  assert.ok(afterFirst >= 1, "first run should have pip-installed");

  const second = await runPostinstall(env);
  assert.equal(second.code, 0);
  const afterSecond = fs.readFileSync(pipLog, "utf8").split("\n").filter(Boolean).length;
  // The version stamp makes the second run a no-op. This is what keeps
  // `npm i -g` cheap and what the old network-install test could not check.
  assert.equal(afterSecond, afterFirst, "second run should not re-run pip");
  fs.rmSync(home, { recursive: true, force: true });
  fs.rmSync(artifact.dir, { recursive: true, force: true });
});

test("postinstall reports a failing interpreter without a non-zero exit", { skip: hermeticOnly }, async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-nopy-"));
  // Pin PATH to a directory containing no interpreter or `node`, so the
  // bootstrap cannot fall back to the machine's real `python3` (with the
  // normal PATH it used to, which is how the old test quietly performed a
  // real PyPI install inside a temp home pretending to check a failure path).
  const emptyBin = path.join(home, "empty-bin");
  fs.mkdirSync(emptyBin, { recursive: true });
  const { code, stderr } = await runPostinstall({
    ITERATE_HARNESS_NPM_HOME: home,
    ITERATE_HARNESS_SKIP_INSTALL: "",
    ITERATE_HARNESS_PYTHON: path.join(home, "definitely-not-a-python"),
    PATH: emptyBin,
  });
  // A machine with no usable Python is a normal postinstall outcome, not a
  // failed `npm install`: the wrapper prints guidance and exits 0.
  assert.equal(code, 0);
  assert.match(stderr, /could not install the harness/i);
  fs.rmSync(home, { recursive: true, force: true });
});

test("bootstrap modules load and expose ensureRuntime (smoke)", async () => {
  const mod = require(BIN_BOOTSTRAP);
  assert.equal(typeof mod.ensureRuntime, "function");
  assert.equal(typeof mod.reportBootstrapFailure, "function");
});
