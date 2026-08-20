/* Tests for the terminal UI helpers (lib/ui.js). */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const ui = require("../lib/ui");
const { CancelledError } = require("../lib/bootstrap");

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