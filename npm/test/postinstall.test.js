/* Tests for the npm postinstall hook (scripts/postinstall.js). */

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

const nodeBin = process.execPath;

function runPostinstall(env) {
  return new Promise((resolve) => {
    execFile(nodeBin, [POSTINSTALL], { env: { ...process.env, ...env } }, (error, stdout, stderr) => {
      resolve({ code: error ? error.code ?? 1 : 0, stdout, stderr });
    });
  });
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

test("postinstall delegates to bootstrap ensureRuntime", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "ih-ps-boot-"));
  const { code, stderr } = await runPostinstall({
    ITERATE_HARNESS_NPM_HOME: home,
    ITERATE_HARNESS_SKIP_INSTALL: "",
  });
  // Regardless of whether the full install succeeds in this sandbox, the hook
  // must never crash: it either reports success or swallows the failure.
  assert.equal(code, 0);
  assert.match(stderr, /installed during npm install|could not install the harness/i);
  fs.rmSync(home, { recursive: true, force: true });
});

test("bootstrap modules load and expose ensureRuntime (smoke)", async () => {
  const mod = require(BIN_BOOTSTRAP);
  assert.equal(typeof mod.ensureRuntime, "function");
  assert.equal(typeof mod.reportBootstrapFailure, "function");
});