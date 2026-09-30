#!/usr/bin/env node
/* Fake Python interpreter for hermetic npm-wrapper tests.
 *
 * The postinstall test used to shell out to the real interpreter and run a
 * real `pip install iterate-harness==X.Y.Z` against PyPI inside a temp home.
 * That made `npm test` a network operation: it took minutes on a cold cache,
 * failed outright on an offline/CI runner, and — when the install partially
 * succeeded — left a multi-megabyte venv per run in $TMPDIR. The suite hung
 * indefinitely, which is why `postinstall.test.js` had to be killed.
 *
 * This stand-in speaks just enough of the protocol `lib/bootstrap.js` uses:
 *
 *   python --version                 -> "Python 3.12.0"
 *   python -m venv <dir>             -> <dir>/bin/{activate,python,ih}
 *   <dir>/bin/python -m pip install  -> success message
 *
 * Nothing here is a Python implementation; it exists so the bootstrap's
 * control flow (detect -> venv -> pip -> stamp) can be exercised offline and
 * in milliseconds. The real interpreter is still used by every non-test code
 * path.
 */

"use strict";

const fs = require("node:fs");
const path = require("node:path");

const FAKE_VERSION = "3.12.0";
const IS_WINDOWS = process.platform === "win32";
const BIN_DIR = IS_WINDOWS ? "Scripts" : "bin";

function venvLayout(venvDir) {
  const root = path.resolve(venvDir);
  const bin = path.join(root, BIN_DIR);
  return {
    root,
    bin,
    python: path.join(bin, "python"),
    activate: path.join(bin, "activate"),
    ih: path.join(bin, process.platform === "win32" ? "ih.cmd" : "ih"),
  };
}

function writeExecutable(target, contents) {
  fs.writeFileSync(target, contents, { mode: 0o755 });
  try {
    fs.chmodSync(target, 0o755);
  } catch {
    /* chmod is best-effort on exotic filesystems */
  }
}

function createVenv(venvDir) {
  const layout = venvLayout(venvDir);
  fs.mkdirSync(layout.bin, { recursive: true });

  // The bootstrap re-checks the interpreter path after `-m venv`, so the
  // fake interpreter must exist there too. A copy (not a symlink) keeps the
  // fixture working on Windows and on filesystems without symlinks.
  writeExecutable(layout.python, fs.readFileSync(__filename, "utf8"));
  writeExecutable(layout.activate, "# fake venv activate\n");

  // `ensureRuntime` refuses to continue unless <venv>/bin/ih exists, so the
  // fake venv ships one that reports the package version.
  const ihScript =
    "#!/usr/bin/env node\n" +
    'process.stdout.write("iterate-harness (fake venv)\\n");\n';
  writeExecutable(layout.ih, ihScript);
  if (process.platform === "win32") {
    fs.writeFileSync(
      path.join(layout.bin, "ih.cmd"),
      "@echo off\r\nnode \"%~dp0ih\" %*\r\n",
      "utf8"
    );
  }
  return layout;
}

function main(argv) {
  const args = argv.slice(2);

  if (args.includes("--version") || args.includes("-V")) {
    process.stdout.write(`Python ${FAKE_VERSION}\n`);
    return 0;
  }

  if (args[0] === "-m") {
    const module = args[1];
    if (module === "venv") {
      const target = args[2];
      if (!target) {
        process.stderr.write("fake-python: -m venv requires a directory\n");
        return 2;
      }
      createVenv(target);
      return 0;
    }
    if (module === "pip") {
      // Record the call so a test can assert on what pip was asked to do.
      const logFile = process.env.ITERATE_HARNESS_FAKE_PIP_LOG;
      if (logFile) {
        fs.appendFileSync(logFile, `${args.join(" ")}\n`, "utf8");
      }
      const target = args[args.length - 1] || "";
      process.stdout.write(
        `Successfully installed iterate-harness-${target.replace(/[^0-9.]/g, "") || "0.0.0"}\n`
      );
      return 0;
    }
    process.stdout.write(`fake-python: unhandled module ${module}\n`);
    return 0;
  }

  // Invoked as the venv's `ih` entry point.
  process.stdout.write("iterate-harness (fake venv)\n");
  return 0;
}

process.exitCode = main(process.argv);
