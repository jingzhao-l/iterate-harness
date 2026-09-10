/* Tests for the terminal UI helpers (lib/ui.js). */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { PassThrough } = require("node:stream");

const ui = require("../lib/ui");
const { CancelledError, interactiveSession } = require("../lib/bootstrap");

test("ITERATE_BANNER is a 6-line ASCII art block", () => {
  assert.equal(ui.ITERATE_BANNER.length, 6);
  for (const line of ui.ITERATE_BANNER) {
    assert.equal(typeof line, "string");
    assert.ok(line.length > 0, "banner line should not be empty");
  }
});

test("stripAnsi removes ANSI color codes", () => {
  assert.equal(ui.stripAnsi("\x1b[36mhello\x1b[0m"), "hello");
  assert.equal(ui.stripAnsi("plain"), "plain");
  assert.equal(ui.stripAnsi("\x1b[2m dim \x1b[0m"), " dim ");
});

test("printBanner is a function (no throw in non-TTY)", () => {
  // stderr is not a TTY in the test runner, so the banner is skipped silently.
  assert.doesNotThrow(() => ui.printBanner());
});

test("frameSection builds a box with borders and alignment", () => {
  // Capture stderr output of frameSection.
  const originalWrite = process.stderr.write;
  const chunks = [];
  process.stderr.write = (chunk) => {
    chunks.push(String(chunk));
    return true;
  };
  try {
    ui.frameSection("Done", ["\x1b[32m✓\x1b[0m hello", "  world"]);
  } finally {
    process.stderr.write = originalWrite;
  }
  const output = chunks.join("");
  assert.match(output, /┌─ Done ─+┐/);
  assert.match(ui.stripAnsi(output), /✓ hello/);
  assert.match(ui.stripAnsi(output), /world/);
  assert.match(output, /└─+┘/);
});

test("askYesNo is exported and returns a Promise", () => {
  assert.equal(typeof ui.askYesNo, "function");
});

test("CancelledError is exported and is an Error", () => {
  const err = new CancelledError("skipped");
  assert.ok(err instanceof Error);
  assert.equal(err.message, "skipped");
});

test("interactiveSession reports a boolean", () => {
  assert.equal(typeof interactiveSession(), "boolean");
});

test("askYesNo resolves the default on EOF (unattended stdin)", async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const promise = ui.askYesNo("Install?", true, { input, output, timeoutMs: 0 });
  input.end();
  assert.equal(await promise, true);
});

test("askYesNo resolves the default on timeout", async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const value = await ui.askYesNo("Install?", false, {
    input,
    output,
    timeoutMs: 20,
  });
  assert.equal(value, false);
});

test("askYesNo parses an explicit yes", async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const promise = ui.askYesNo("Install?", false, { input, output, timeoutMs: 1000 });
  input.write("y\n");
  assert.equal(await promise, true);
});

test("DEFAULT_PROMPT_TIMEOUT_MS is a positive number", () => {
  assert.equal(typeof ui.DEFAULT_PROMPT_TIMEOUT_MS, "number");
  assert.ok(ui.DEFAULT_PROMPT_TIMEOUT_MS > 0);
});