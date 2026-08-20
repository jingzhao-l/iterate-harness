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
const ui = require("../lib/ui");

async function main() {
  if (process.env[SKIP_INSTALL_ENV_VAR] === "1") {
    ui.warning(`Skipped install during npm install (${SKIP_INSTALL_ENV_VAR}=1); harness installs on the first \`ih\` run.`);
    return;
  }
  try {
    await ensureRuntime();
    ui.success("iterate-harness installed during npm install — `ih` is ready to use.");
  } catch (error) {
    reportBootstrapFailure(error);
    ui.warning(
      "Could not install the harness during npm install (see above). " +
        "It will be retried automatically on the first `ih` run."
    );
  }
}

main().catch((error) => {
  reportBootstrapFailure(error);
  process.exit(0);
});