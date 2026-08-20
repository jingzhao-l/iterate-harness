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
 *    (lockstep: npm 1.12.6 installs harness v1.12.6). The preferred artifact is
 *    the pre-built wheel uploaded to the GitHub release (it already contains
 *    the compiled frontend assets, exactly like iterate-skill-installer ships
 *    pre-wrapped assets). pip-installing the wheel avoids building from source,
 *    which previously failed because the source archive has no frontend/web/
 *    dist for pyproject.toml's force-include. When pip cannot fetch the wheel
 *    (typically a broken Python TLS trust store on macOS python.org builds),
 *    the wrapper retries by downloading the wheel with Node's own https stack
 *    — and, failing that, with curl (system trust store) — then pip-installs
 *    the local file. If the wheel is missing from the release entirely, the
 *    wrapper falls back to the pinned source archive as a last resort.
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

// Preferred install URL: the pre-built wheel pinned to this npm version.
// An explicit env override always wins (e.g. a local dev wheel or git ref).
function installUrl(version, env) {
  const override = env ? env[INSTALL_URL_ENV_VAR] : undefined;
  if (override) {
    return override;
  }
  return wheelAssetUrl(version);
}

// Last-resort fallback URL: the pinned source archive. Only relevant when the
// user did not override the install URL (an explicit override means they own
// the source and no archive fallback should kick in).
function installFallbackUrl(version, env) {
  const override = env ? env[INSTALL_URL_ENV_VAR] : undefined;
  if (override) {
    return null;
  }
  return releaseTarballUrl(version);
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
  const url = options.url;
  const homeDir = options.homeDir;
  const version = options.version;
  const runStepFn = options.runStepFn || runStep;
  const downloader = options.downloader || downloadTarballTo;
  // Optional terminal fallback (e.g. the source archive if the wheel is missing).
  const fallback = options.fallback;

  try {
    runStepFn(python, pipInstallArgs(url));
    return;
  } catch (primaryError) {
    if (!isRemoteHttpUrl(url)) {
      throw primaryError;
    }
    process.stderr.write(
      `[iterate-harness] pip install from ${url} failed; retrying via direct download ...\n`
    );
    const cachePath = downloadCachePath(homeDir, version, artifactExtensionFor(url));
    try {
      await downloader(url, cachePath);
    } catch (downloadError) {
      if (fallback) {
        process.stderr.write(
          `[iterate-harness] primary artifact unavailable (${downloadError.message}); ` +
            `falling back to source archive ${fallback.url} ...\n`
        );
        return installHarness(fallback);
      }
      throw new BootstrapError(
        `${primaryError.message}\n\n(direct-download fallback also failed: ${downloadError.message})`
      );
    }
    runStepFn(python, pipInstallArgs(cachePath));
  }
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
    process.stderr.write(`[iterate-harness] creating virtualenv at ${venvDir} ...\n`);
    runStep(interpreter.command, [...interpreter.preArgs, "-m", "venv", venvDir]);
  }

  const url = installUrl(version, env);
  const fallbackUrl = installFallbackUrl(version, env);
  process.stderr.write(`[iterate-harness] installing iterate-harness ${version} ...\n`);
  await installHarness({
    python: executable.python,
    url,
    homeDir,
    version,
    fallback:
      fallbackUrl != null
        ? { python: executable.python, url: fallbackUrl, homeDir, version }
        : undefined,
  });

  const stampPath = path.join(homeDir, STAMP_FILE_NAME);
  fs.writeFileSync(stampPath, `${version}\n`, "utf8");
}

async function ensureRuntime(env) {
  const environment = env || process.env;
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
    await bootstrap(homeDir, version, environment);
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
  let target;
  try {
    target = await ensureRuntime(env);
  } catch (error) {
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
  installFallbackUrl,
  installHarness,
  installUrl,
  isRemoteHttpUrl,
  isSupportedPython,
  needsBootstrap,
  packageVersion,
  parsePythonVersion,
  pipInstallArgs,
  pythonCandidates,
  releaseTarballUrl,
  reportBootstrapFailure,
  runHarness,
  venvExecutablePaths,
  wheelAssetName,
  wheelAssetUrl,
};
