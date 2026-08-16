#!/usr/bin/env node
/* `ih` — global entry point of the iterate-harness npm wrapper. */

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
