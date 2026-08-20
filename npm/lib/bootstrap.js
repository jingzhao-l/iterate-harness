/*
 * Node-side bootstrap for the iterate-harness npm distribution wrapper.
 *
 * The harness itself is a Python package (the `ih` CLI). This module never
 * re-implements harness logic — it only prepares a managed virtualenv and
 * delegates every invocation to the real `ih` executable inside it:
 *
 * 1. Resolve a Python interpreter >= 3.10 (env override first).
 * 2. Create/reuse the venv at ~/.iterate-harness-npm/venv.
 * 3. pip-install the harness release pinned to THIS npm package's version
 *    (lockstep: npm 1.12.9 installs harness v1.12.9). The preferred source is
 *    the official **PyPI** index — pip resolves `iterate-harness==X.Y.Z` against
 *    the user's configured mirror (e.g. `pypi.tuna.tsinghua.edu.cn`), so it
 *    works even when GitHub is unreachable or its TLS cert fails to verify.
 *    If PyPI is unavailable, the wrapper falls back to the pre-built GitHub
 *    **wheel** release asset (frontend dist baked in, like iterate-skill-
 *    installer ships pre-wrapped assets), pip-installing it directly and, if
 *    pip's trust store is broken (common on macOS python.org builds), retrying
 *    by downloading the wheel with Node's own https stack — and, failing that,
 *    with curl (system trust store) — then pip-installing the local file. If
 *    the wheel is missing too, the worker falls back to the pinned source
 *    archive as a final resort.
 * 4. Stamp the installed version; a mismatch (npm upgrade) triggers a
 *    re-install on the next run.
 * 5. Spawn the venv's `ih` with argv/stdio/signals forwarded and the exit
 *    code passed through.
 *
 * Failures print an actionable message and exit 1 — never a raw stack trace.
 */

"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const https = require("https");
const os = require("os");
const path = require("path");

const ui = require("./ui");

// ---------------------------------------------------------------------------
// Constants (no magic strings below — everything is named here)
// ---------------------------------------------------------------------------

const RUNTIME_DIR_NAME = ".iterate-harness-npm";
const VENV_DIR_NAME = "venv";
const STAMP_FILE_NAME = "version.stamp";
const TARBALL_CACHE_DIR_NAME = "cache";

const MAX_DOWNLOAD_REDIRECTS = 5;
const DOWNLOAD_TIMEOUT_MS = 120000;
const CURL_MAX_TIME_SECONDS = 240;

const PYTHON_ENV_VAR = "ITERATE_HARNESS_PYTHON";
const HOME_ENV_VAR = "ITERATE_HARNESS_NPM_HOME";
const INSTALL_URL_ENV_VAR = "ITERATE_HARNESS_INSTALL_URL";
const SKIP_INSTALL_ENV_VAR = "ITERATE_HARNESS_SKIP_INSTALL";

const HARNESS_GITHUB_REPO = "jingzhao-l/iterate-harness";
const HARNESS_RELEASE_DOWNLOAD_URL = `https://github.com/${HARNESS_GITHUB_REPO}/releases/download`;
const HARNESS_REPO_ARCHIVE_URL = `https://github.com/${HARNESS_GITHUB_REPO}/archive/refs/tags`;

// Preferred install channel: the official PyPI index. pip reads the user's
// mirror config (e.g. `pypi.tuna.tsinghua.edu.cn`), so it stays fast and
// stable even when GitHub is unreachable — unlike the GitHub wheel fallback.
const PYPI_PACKAGE_NAME = "iterate-harness";

// Preferred installable artifact: the pre-built wheel (frontend dist baked in).
const WHEEL_ASSET_SUFFIX = "-py3-none-any.whl";
// Last-resort artifact: the pinned source archive (pip builds from source).
const DEFAULT_ARTIFACT_EXT = ".tar.gz";
const WHEEL_ARTIFACT_EXT = ".whl";

const MIN_PYTHON_MAJOR = 3;
const MIN_PYTHON_MINOR = 10;

const PYTHON_VERSION_PATTERN = /Python\s+(\d+)\.(\d+)\.(\d+)/;
const IS_WINDOWS = process.platform === "win32";

class BootstrapError extends Error {}

// Thrown when the user declines the interactive install wizard. Treated as a
// graceful "nothing to do" exit (code 0), never as a failure.
class CancelledError extends Error {}

// ---------------------------------------------------------------------------
// Pure helpers (unit-tested in test/bootstrap.test.js)
// ---------------------------------------------------------------------------

function wheelAssetName(version) {
  return `iterate_harness-${version}${WHEEL_ASSET_SUFFIX}`;
}

// Preferred installable source: the pre-built wheel (frontend dist baked in).
function wheelAssetUrl(version) {
  return `${HARNESS_RELEASE_DOWNLOAD_URL}/v${version}/${wheelAssetName(version)}`;
}

// PyPI package spec pinned to the version (e.g. `iterate-harness==1.12.9`).
// pip resolves this against the user's configured index/mirror, not GitHub.
function pypiInstallSpec(version) {
  return `${PYPI_PACKAGE_NAME}==${version}`;
}

// Last-resort installable source: the pinned source archive (builds on pip).
function releaseTarballUrl(version) {
  return `${HARNESS_REPO_ARCHIVE_URL}/v${version}.tar.gz`;
}

function parsePythonVersion(output) {
  const match = PYTHON_VERSION_PATTERN.exec(String(output));
  if (!match) {
    return null;
  }
  return {
    major: Number.parseInt(match[1], 10),
    minor: Number.parseInt(match[2], 10),
    patch: Number.parseInt(match[3], 10),
  };
}

function isSupportedPython(version) {
  if (!version) {
    return false;
  }
  return (
    version.major > MIN_PYTHON_MAJOR ||
    (version.major === MIN_PYTHON_MAJOR && version.minor >= MIN_PYTHON_MINOR)
  );
}

function pythonCandidates(platform, env) {
  const override = env ? env[PYTHON_ENV_VAR] : undefined;
  const candidates = [];
  if (override) {
    candidates.push({ command: override, preArgs: [] });
  }
  if (platform === "win32") {
    candidates.push({ command: "py", preArgs: ["-3"] });
    candidates.push({ command: "python", preArgs: [] });
  } else {
    candidates.push({ command: "python3", preArgs: [] });
    candidates.push({ command: "python", preArgs: [] });
  }
  return candidates;
}

function venvExecutablePaths(venvDir, platform) {
  if (platform === "win32") {
    return {
      python: path.join(venvDir, "Scripts", "python.exe"),
      ih: path.join(venvDir, "Scripts", "ih.exe"),
    };
  }
  return {
    python: path.join(venvDir, "bin", "python"),
    ih: path.join(venvDir, "bin", "ih"),
  };
}

function needsBootstrap(stampContent, expectedVersion) {
  return String(stampContent || "").trim() !== String(expectedVersion || "").trim();
}

function isRemoteHttpUrl(target) {
  return /^https?:\/\//i.test(String(target || ""));
}

// Infer the on-disk extension (for the cache file) from a download URL so
// pip-installing the local copy keeps a correct .whl / .tar.gz suffix.
function artifactExtensionFor(url) {
  const match = /\.(tar\.gz|whl)$/i.exec(String(url || ""));
  if (!match) {
    return DEFAULT_ARTIFACT_EXT;
  }
  return match[1].toLowerCase() === "whl" ? WHEEL_ARTIFACT_EXT : DEFAULT_ARTIFACT_EXT;
}

function downloadCachePath(homeDir, version, extension) {
  const ext = extension || DEFAULT_ARTIFACT_EXT;
  if (ext === WHEEL_ARTIFACT_EXT) {
    // A locally-cached wheel must keep a valid PEP 427 filename (e.g.
    // `iterate_harness-1.12.6-py3-none-any.whl`). pip refuses any `*.whl`
    // whose name lacks the `{python}-{abi}-{platform}` tags, so the fallback
    // cache file reuses the real release asset name rather than a bare
    // `iterate-harness-{version}.whl`.
    return path.join(homeDir, TARBALL_CACHE_DIR_NAME, wheelAssetName(version));
  }
  return path.join(homeDir, TARBALL_CACHE_DIR_NAME, `iterate-harness-${version}${ext}`);
}

function pipInstallArgs(target) {
  return ["-m", "pip", "install", "--upgrade", "--force-reinstall", target];
}

// ---------------------------------------------------------------------------
// Runtime resolution
// ---------------------------------------------------------------------------

function packageVersion() {
  const manifestPath = path.resolve(__dirname, "..", "package.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  return manifest.version;
}

function runtimeHomeDir(env) {
  const override = env ? env[HOME_ENV_VAR] : undefined;
  if (override) {
    return override;
  }
  return path.join(os.homedir(), RUNTIME_DIR_NAME);
}

function detectPython(env) {
  const errors = [];
  for (const candidate of pythonCandidates(process.platform, env || process.env)) {
    const probe = spawnSync(candidate.command, [...candidate.preArgs, "--version"], {
      encoding: "utf8",
      windowsHide: true,
    });
    if (probe.error) {
      errors.push(`${candidate.command}: not found`);
      continue;
    }
    const stdout = `${probe.stdout || ""}${probe.stderr || ""}`;
    const version = parsePythonVersion(stdout);
    if (!version) {
      errors.push(`${candidate.command}: unreadable version output`);
      continue;
    }
    if (!isSupportedPython(version)) {
      errors.push(
        `${candidate.command}: ${version.major}.${version.minor}.${version.patch} is older than 3.10`
      );
      continue;
    }
    return { ...candidate, version };
  }
  throw new BootstrapError(
    [
      "Python >= 3.10 not found (tried: " + errors.join("; ") + ").",
      "",
      "Install Python 3.10+ or point the wrapper at an existing interpreter:",
      `  ${PYTHON_ENV_VAR}=/path/to/python3.12 npx iterate-harness --version`,
    ].join("\n")
  );
}

// ---------------------------------------------------------------------------
// Bootstrap (venv + pip install of the pinned release tarball)
// ---------------------------------------------------------------------------

const CERT_VERIFY_FAILURE_MARKER = "CERTIFICATE_VERIFY_FAILED";

function runStep(command, args, options) {
  const result = spawnSync(command, args, {
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
    maxBuffer: 16 * 1024 * 1024,
    ...options,
  });
  const capturedOutput = `${result.stdout || ""}${result.stderr || ""}`;
  if (capturedOutput) {
    process.stderr.write(capturedOutput);
  }
  if (result.error) {
    throw new BootstrapError(`failed to run ${command}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    if (capturedOutput.includes(CERT_VERIFY_FAILURE_MARKER)) {
      throw new BootstrapError(
        [
          `${command} ${args.join(" ")} exited with code ${result.status}`,
          "",
          "The download failed TLS certificate verification. This is a local",
          "Python/pip trust-store issue, not a harness problem. Common fixes:",
          "  - macOS python.org installs: run",
          "      /Applications/Python 3.1x/Install Certificates.command",
          "  - Point pip at a CA bundle:  export PIP_CERT=$(python3 -m certifi)",
          `  - Or use another interpreter: ${PYTHON_ENV_VAR}=/opt/homebrew/bin/python3.12 npm i -g iterate-harness`,
        ].join("\n")
      );
    }
    throw new BootstrapError(`${command} ${args.join(" ")} exited with code ${result.status}`);
  }
}

// Ordered install candidates, best-effort first:
//   1. PyPI spec — pip installs via the user's mirror (stable, no GitHub).
//   2. Pre-built wheel on the GitHub release (needs GitHub, but no source build).
//   3. Pinned source archive on GitHub (last resort; pip builds from source).
// An explicit env override short-circuits the chain (the user owns the source).
function installCandidates(version, env) {
  const override = env ? env[INSTALL_URL_ENV_VAR] : undefined;
  if (override) {
    return [override];
  }
  return [pypiInstallSpec(version), wheelAssetUrl(version), releaseTarballUrl(version)];
}

// ---------------------------------------------------------------------------
// Artifact download fallback (Node-side TLS — survives broken pip trust stores)
// ---------------------------------------------------------------------------

function downloadFile(url, dest, redirectBudget) {
  const budget = redirectBudget === undefined ? MAX_DOWNLOAD_REDIRECTS : redirectBudget;
  return new Promise((resolve, reject) => {
    if (budget < 0) {
      reject(new BootstrapError(`too many redirects while downloading ${url}`));
      return;
    }
    let parsed;
    try {
      parsed = new URL(url);
    } catch (error) {
      reject(new BootstrapError(`invalid download URL ${url}: ${error.message}`));
      return;
    }
    const client = parsed.protocol === "http:" ? http : https;
    const request = client.get(url, { timeout: DOWNLOAD_TIMEOUT_MS }, (response) => {
      const status = response.statusCode || 0;
      if (status >= 300 && status < 400 && response.headers.location) {
        response.resume();
        const nextUrl = new URL(response.headers.location, url).toString();
        resolve(downloadFile(nextUrl, dest, budget - 1));
        return;
      }
      if (status < 200 || status >= 300) {
        response.resume();
        reject(new BootstrapError(`downloading ${url} failed with HTTP ${status}`));
        return;
      }
      const file = fs.createWriteStream(dest);
      response.pipe(file);
      file.on("finish", () => {
        file.close((closeError) => {
          if (closeError) {
            reject(closeError);
            return;
          }
          resolve(dest);
        });
      });
      file.on("error", (fileError) => {
        file.destroy();
        reject(fileError);
      });
    });
    request.on("timeout", () => {
      request.destroy(new BootstrapError(`downloading ${url} timed out`));
    });
    request.on("error", (requestError) => reject(requestError));
  });
}

function curlDownload(url, dest, spawnFn) {
  const runner = spawnFn || spawnSync;
  const result = runner("curl", [
    "-fsSL",
    "--max-time",
    String(CURL_MAX_TIME_SECONDS),
    "-o",
    dest,
    url,
  ], {
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  if (result.error) {
    return {
      ok: false,
      error: new BootstrapError(`curl download of ${url} failed: ${result.error.message}`),
    };
  }
  if (result.status !== 0) {
    const detail = `${result.stderr || result.stdout || ""}`.trim() || `exit code ${result.status}`;
    return { ok: false, error: new BootstrapError(`curl download of ${url} failed: ${detail}`) };
  }
  return { ok: true, error: undefined };
}

async function downloadTarballTo(url, dest, deps) {
  const nodeDownload = (deps && deps.nodeDownload) || downloadFile;
  const curlDownloadFn = (deps && deps.curlDownload) || curlDownload;
  fs.mkdirSync(path.dirname(dest), { recursive: true });

  const attempts = [
    {
      name: "node-https",
      run: () => nodeDownload(url, dest),
    },
    {
      name: "curl",
      run: async () => {
        const outcome = curlDownloadFn(url, dest);
        if (!outcome.ok) {
          throw outcome.error;
        }
        return dest;
      },
    },
  ];

  const failures = [];
  for (const attempt of attempts) {
    try {
      await attempt.run();
      return dest;
    } catch (error) {
      failures.push(`${attempt.name}: ${error.message}`);
      process.stderr.write(
        `[iterate-harness] ${attempt.name} download failed (${error.message}); trying next strategy ...\n`
      );
      fs.rmSync(dest, { force: true });
    }
  }
  throw new BootstrapError(`direct download of ${url} failed (${failures.join("; ")})`);
}

// ---------------------------------------------------------------------------
// Install chain: pip from URL (pre-built wheel) first, Node-download + local
// pip as TLS fallback; missing wheel falls back to the source archive.
// ---------------------------------------------------------------------------

async function installHarness(options) {
  const python = options.python;
  const homeDir = options.homeDir;
  const version = options.version;
  const candidates = options.candidates;
  const runStepFn = options.runStepFn || runStep;
  const downloader = options.downloader || downloadTarballTo;

  if (!candidates || candidates.length === 0) {
    throw new BootstrapError("no install candidate URLs were provided");
  }

  const failures = [];
  for (const candidate of candidates) {
    try {
      if (isRemoteHttpUrl(candidate)) {
        // HTTP(S) candidate: `pip` first, then node/curl download + local pip
        // (survives broken Python TLS trust stores on macOS python.org builds).
        await installRemoteArtifact(python, candidate, homeDir, version, runStepFn, downloader);
      } else {
        // Non-URL candidate (e.g. `iterate-harness==1.12.9` resolved by pip
        // against the user's mirror): no artifact to download, just pip it.
        runStepFn(python, pipInstallArgs(candidate));
      }
      return;
    } catch (error) {
      failures.push(`${candidate}: ${error.message}`);
      process.stderr.write(
        `[iterate-harness] install from "${candidate}" failed (${error.message}); ` +
          "trying the next candidate ...\n"
      );
    }
  }

  throw new BootstrapError(`all install candidates failed:\n  ${failures.join("\n  ")}`);
}

// pip-install a downloadable HTTP artifact, retrying with a node/curl download
// of the raw file + local pip install when pip's TLS trust store is broken.
async function installRemoteArtifact(python, url, homeDir, version, runStepFn, downloader) {
  try {
    runStepFn(python, pipInstallArgs(url));
    return;
  } catch (primaryError) {
    process.stderr.write(
      `[iterate-harness] pip install from ${url} failed; retrying via direct download ...\n`
    );
  }
  const cachePath = downloadCachePath(homeDir, version, artifactExtensionFor(url));
  await downloader(url, cachePath);
  runStepFn(python, pipInstallArgs(cachePath));
}

async function bootstrap(homeDir, version, env) {
  fs.mkdirSync(homeDir, { recursive: true });

  const venvDir = path.join(homeDir, VENV_DIR_NAME);
  const executable = venvExecutablePaths(venvDir, process.platform);
  const activateMarker = IS_WINDOWS
    ? path.join(venvDir, "Scripts", "activate")
    : path.join(venvDir, "bin", "activate");

  const interpreter = detectPython(env);
  const recreateVenv = fs.existsSync(venvDir) && !fs.existsSync(activateMarker);
  if (recreateVenv) {
    fs.rmSync(venvDir, { recursive: true, force: true });
  }
  if (!fs.existsSync(activateMarker)) {
    ui.step(`Creating virtualenv at ${venvDir}`);
    runStep(interpreter.command, [...interpreter.preArgs, "-m", "venv", venvDir]);
  }

  const candidates = installCandidates(version, env);
  ui.step(`Installing iterate-harness ${version}`);
  await installHarness({
    python: executable.python,
    candidates,
    homeDir,
    version,
  });

  const stampPath = path.join(homeDir, STAMP_FILE_NAME);
  fs.writeFileSync(stampPath, `${version}\n`, "utf8");
  ui.success(`iterate-harness ${version} installed`);
}

async function ensureRuntime(env, options) {
  const environment = env || process.env;
  const opts = options || {};
  const version = packageVersion();
  const homeDir = runtimeHomeDir(environment);
  const venvDir = path.join(homeDir, VENV_DIR_NAME);
  const executable = venvExecutablePaths(venvDir, process.platform);
  const stampPath = path.join(homeDir, STAMP_FILE_NAME);

  const skipInstall = environment[SKIP_INSTALL_ENV_VAR] === "1";
  const stampContent = fs.existsSync(stampPath)
    ? fs.readFileSync(stampPath, "utf8")
    : "";

  if (!skipInstall && needsBootstrap(stampContent, version)) {
    if (opts.interactive) {
      // Interactive install wizard: ask the user before downloading.
      const confirmed = await ui.askYesNo(
        `Install iterate-harness v${version} into ${homeDir}?`,
        true
      );
      if (!confirmed) {
        throw new CancelledError(
          "Skipped installing iterate-harness. Re-run `ih` whenever you are ready."
        );
      }
      ui.frameSection("Installing", [
        `\x1b[36m◆\x1b[0m Version: v${version}`,
        `\x1b[36m◆\x1b[0m Runtime:  ${homeDir}`,
      ]);
    }
    await bootstrap(homeDir, version, environment);
    if (opts.interactive) {
      ui.frameSection("Done", [
        `\x1b[32m✓\x1b[0m iterate-harness v${version} installed`,
        `  Run \x1b[36mih --help\x1b[0m to see all commands.`,
        `  Try \x1b[36mih status\x1b[0m or \x1b[36mih iterate --help\x1b[0m to get started.`,
      ]);
    }
  }

  if (!fs.existsSync(executable.ih)) {
    throw new BootstrapError(
      [
        `ih is not installed yet at ${executable.ih}.`,
        skipInstall
          ? `Bootstrap was skipped (${SKIP_INSTALL_ENV_VAR}=1) — unset it and retry.`
          : "The installation may have failed; remove the runtime dir and retry:",
        `  rm -rf ${homeDir}`,
      ].join("\n")
    );
  }
  return executable.ih;
}

// ---------------------------------------------------------------------------
// Delegation to the real ih executable
// ---------------------------------------------------------------------------

function reportBootstrapFailure(error) {
  const message = error instanceof BootstrapError ? error.message : (error && error.stack) || error;
  process.stderr.write(`[iterate-harness] ${message}\n`);
}

async function runHarness(args, env) {
  // Print the banner on every run (claude-code style). Skipped automatically
  // when stderr is not a TTY (e.g. piped output), so `ih --version | jq` stays clean.
  ui.printBanner();

  let target;
  try {
    target = await ensureRuntime(env, { interactive: true });
  } catch (error) {
    if (error instanceof CancelledError) {
      ui.info(error.message);
      process.exit(0);
      return;
    }
    reportBootstrapFailure(error);
    process.exit(1);
    return;
  }
  const child = spawn(target, args, {
    stdio: "inherit",
    windowsHide: false,
    env: env || process.env,
  });

  const forwardSignal = (signal) => {
    if (!child.killed) {
      child.kill(signal);
    }
  };
  process.on("SIGINT", () => forwardSignal("SIGINT"));
  process.on("SIGTERM", () => forwardSignal("SIGTERM"));

  child.on("error", (error) => {
    process.stderr.write(`[iterate-harness] failed to launch ih: ${error.message}\n`);
    process.exit(1);
  });
  child.on("close", (code, signal) => {
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code === null ? 1 : code);
  });
}

module.exports = {
  BootstrapError,
  CancelledError,
  DEFAULT_ARTIFACT_EXT,
  MAX_DOWNLOAD_REDIRECTS,
  MIN_PYTHON_MAJOR,
  MIN_PYTHON_MINOR,
  PYTHON_ENV_VAR,
  HOME_ENV_VAR,
  INSTALL_URL_ENV_VAR,
  SKIP_INSTALL_ENV_VAR,
  WHEEL_ASSET_SUFFIX,
  artifactExtensionFor,
  detectPython,
  curlDownload,
  downloadCachePath,
  downloadFile,
  downloadTarballTo,
  ensureRuntime,
  installCandidates,
  installHarness,
  installRemoteArtifact,
  isRemoteHttpUrl,
  isSupportedPython,
  needsBootstrap,
  packageVersion,
  parsePythonVersion,
  pipInstallArgs,
  pypiInstallSpec,
  pythonCandidates,
  releaseTarballUrl,
  reportBootstrapFailure,
  runHarness,
  venvExecutablePaths,
  wheelAssetName,
  wheelAssetUrl,
};
