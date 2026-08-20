#!/usr/bin/env node
/*
 * npm `postinstall` hook for the iterate-harness wrapper.
 *
 * Runs right after `npm install`/`npm install -g` finishes, so the Python
 * harness is ready the moment npm finishes — no wait on the first `ih` run.
 *
 * It deliberately NEVER fails the npm install: if the managed venv cannot be
 * created here (e.g. no Python interpreter, no network, user opted out with
 * ITERATE_HARNESS_SKIP_INSTALL=1), we print a notice and exit 0. The regular
 * `ih` entry point keeps its own lazy-bootstrap fallback, so the first run
 * still installs the harness on demand.
 */

"use strict";

const { ensureRuntime, reportBootstrapFailure, SKIP_INSTALL_ENV_VAR } = require("../lib/bootstrap");

async function main() {
  if (process.env[SKIP_INSTALL_ENV_VAR] === "1") {
    process.stderr.write(
      `[iterate-harness] skipped install during npm install (${SKIP_INSTALL_ENV_VAR}=1); ` +
        "the harness will be installed on the first `ih` run.\n"
    );
    return;
  }
  try {
    await ensureRuntime();
    process.stderr.write("[iterate-harness] harness installed during npm install.\n");
  } catch (error) {
    reportBootstrapFailure(error);
    process.stderr.write(
      "[iterate-harness] could not install the harness during npm install " +
        "(see above). It will be retried automatically on the first `ih` run.\n"
    );
  }
}

main().catch((error) => {
  reportBootstrapFailure(error);
  process.exit(0);
});