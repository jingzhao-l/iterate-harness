#!/usr/bin/env node
/* `iterate-harness` — long-form entry point of the npm wrapper (same as `ih`). */

"use strict";

const { reportBootstrapFailure, runHarness } = require("../lib/bootstrap");

const fail = (error) => {
  reportBootstrapFailure(error);
  process.exit(1);
};

try {
  Promise.resolve(runHarness(process.argv.slice(2))).catch(fail);
} catch (error) {
  fail(error);
}
