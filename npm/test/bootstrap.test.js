/* Unit tests for the pure helpers of lib/bootstrap.js (node --test). */

"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");

const {
  DEFAULT_ARTIFACT_EXT,
  BootstrapError,
  HOME_ENV_VAR,
  INSTALL_URL_ENV_VAR,
  MAX_DOWNLOAD_REDIRECTS,
  PYTHON_ENV_VAR,
  artifactExtensionFor,
  curlDownload,
  downloadCachePath,
  downloadFile,
  downloadTarballTo,
  installCandidates,
  installHarness,
  isRemoteHttpUrl,
  isSupportedPython,
  needsBootstrap,
  parsePythonVersion,
  pipInstallArgs,
  pypiInstallSpec,
  pythonCandidates,
  releaseTarballUrl,
  venvExecutablePaths,
  wheelAssetName,
  wheelAssetUrl,
} = require("../lib/bootstrap");

test("parsePythonVersion extracts major/minor/patch from mixed output", () => {
  assert.deepEqual(parsePythonVersion("Python 3.12.4"), {
    major: 3,
    minor: 12,
    patch: 4,
  });
  assert.deepEqual(parsePythonVersion("py: some warning\nPython 3.10.0rc1\n"), {
    major: 3,
    minor: 10,
    patch: 0,
  });
});

test("parsePythonVersion returns null for non-python output", () => {
  assert.equal(parsePythonVersion("node v20.11.0"), null);
  assert.equal(parsePythonVersion(""), null);
  assert.equal(parsePythonVersion(undefined), null);
});

test("isSupportedPython enforces the 3.10 floor", () => {
  assert.equal(isSupportedPython({ major: 3, minor: 10, patch: 0 }), true);
  assert.equal(isSupportedPython({ major: 3, minor: 12, patch: 4 }), true);
  assert.equal(isSupportedPython({ major: 4, minor: 0, patch: 0 }), true);
  assert.equal(isSupportedPython({ major: 3, minor: 9, patch: 9 }), false);
  assert.equal(isSupportedPython(null), false);
});

test("pythonCandidates puts the env override first on every platform", () => {
  const env = { [PYTHON_ENV_VAR]: "/opt/python3.12/bin/python3" };
  for (const platform of ["win32", "linux", "darwin"]) {
    const candidates = pythonCandidates(platform, env);
    assert.equal(candidates[0].command, "/opt/python3.12/bin/python3");
    assert.deepEqual(candidates[0].preArgs, []);
  }
});

test("pythonCandidates uses py -3 first on windows, python3 first elsewhere", () => {
  const windows = pythonCandidates("win32", {});
  assert.equal(windows[0].command, "py");
  assert.deepEqual(windows[0].preArgs, ["-3"]);

  const linux = pythonCandidates("linux", {});
  assert.equal(linux[0].command, "python3");
  assert.deepEqual(linux[0].preArgs, []);
});

test("venvExecutablePaths resolves platform-specific venv layout", () => {
  const windows = venvExecutablePaths(path.join("home", "venv"), "win32");
  assert.equal(windows.python, path.join("home", "venv", "Scripts", "python.exe"));
  assert.equal(windows.ih, path.join("home", "venv", "Scripts", "ih.exe"));

  const posix = venvExecutablePaths(path.join("home", "venv"), "darwin");
  assert.equal(posix.python, path.join("home", "venv", "bin", "python"));
  assert.equal(posix.ih, path.join("home", "venv", "bin", "ih"));
});

test("releaseTarballUrl pins the archive tag matching the wrapper version", () => {
  assert.equal(
    releaseTarballUrl("1.6.0"),
    "https://github.com/jingzhao-l/iterate-harness/archive/refs/tags/v1.6.0.tar.gz"
  );
});

test("wheelAssetName / wheelAssetUrl target the pre-built release wheel", () => {
  assert.equal(wheelAssetName("1.6.0"), "iterate_harness-1.6.0-py3-none-any.whl");
  assert.equal(
    wheelAssetUrl("1.6.0"),
    "https://github.com/jingzhao-l/iterate-harness/releases/download/v1.6.0/iterate_harness-1.6.0-py3-none-any.whl"
  );
});

test("pypiInstallSpec pins the version to the PyPI index", () => {
  assert.equal(pypiInstallSpec("1.12.9"), "iterate-harness==1.12.9");
});

test("installCandidates: PyPI first, then GitHub wheel, then source archive", () => {
  assert.deepEqual(installCandidates("1.6.0", {}), [
    "iterate-harness==1.6.0",
    wheelAssetUrl("1.6.0"),
    releaseTarballUrl("1.6.0"),
  ]);
});

test("installCandidates returns only the override when ITERATE_HARNESS_INSTALL_URL is set", () => {
  const override = "git+https://github.com/jingzhao-l/iterate-harness.git@main";
  assert.deepEqual(installCandidates("1.6.0", { [INSTALL_URL_ENV_VAR]: override }), [override]);
});

test("artifactExtensionFor maps whl and tar.gz, defaulting otherwise", () => {
  assert.equal(artifactExtensionFor("https://x/y.whl"), ".whl");
  assert.equal(artifactExtensionFor("https://x/y.WHL"), ".whl");
  assert.equal(artifactExtensionFor("https://x/y.tar.gz"), ".tar.gz");
  assert.equal(artifactExtensionFor("git+https://x/y.git@main"), DEFAULT_ARTIFACT_EXT);
  assert.equal(artifactExtensionFor(""), DEFAULT_ARTIFACT_EXT);
});

test("needsBootstrap ignores surrounding whitespace and requires exact match", () => {
  assert.equal(needsBootstrap("1.6.0\n", "1.6.0"), false);
  assert.equal(needsBootstrap(" 1.6.0 ", "1.6.0"), false);
  assert.equal(needsBootstrap("1.5.0\n", "1.6.0"), true);
  assert.equal(needsBootstrap("", "1.6.0"), true);
  assert.equal(needsBootstrap(undefined, "1.6.0"), true);
});

test("HOME_ENV_VAR name is stable (documented in README)", () => {
  assert.equal(HOME_ENV_VAR, "ITERATE_HARNESS_NPM_HOME");
});

// ---------------------------------------------------------------------------
// Tarball download fallback (TLS resilience)
// ---------------------------------------------------------------------------

test("isRemoteHttpUrl matches only http(s) URLs", () => {
  assert.equal(isRemoteHttpUrl("https://github.com/x.tar.gz"), true);
  assert.equal(isRemoteHttpUrl("HTTP://example.com/x.tar.gz"), true);
  assert.equal(isRemoteHttpUrl("http://mirror/x.tar.gz"), true);
  assert.equal(isRemoteHttpUrl("git+https://github.com/repo.git@main"), false);
  assert.equal(isRemoteHttpUrl("/tmp/iterate-harness-1.9.1.tar.gz"), false);
  assert.equal(isRemoteHttpUrl("./iterate-harness-1.9.1.tar.gz"), false);
  assert.equal(isRemoteHttpUrl(""), false);
  assert.equal(isRemoteHttpUrl(undefined), false);
});

test("downloadCachePath lands inside the runtime cache dir with the version pinned", () => {
  assert.equal(
    downloadCachePath(path.join("home", "npm"), "1.9.1"),
    path.join("home", "npm", "cache", "iterate-harness-1.9.1.tar.gz")
  );
});

test("downloadCachePath keeps a valid PEP 427 filename for the wheel cache", () => {
  // A wheel cached for offline pip install must carry the
  // `{name}-{version}-{python}-{abi}-{platform}` tags — pip rejects a bare
  // `iterate-harness-1.9.1.whl` with "not a valid wheel filename".
  assert.equal(
    downloadCachePath(path.join("home", "npm"), "1.9.1", ".whl"),
    path.join("home", "npm", "cache", wheelAssetName("1.9.1"))
  );
  // Round-trips through artifactExtensionFor(wheelAssetUrl) end-to-end.
  assert.equal(
    downloadCachePath(path.join("home", "npm"), "1.9.1", artifactExtensionFor(wheelAssetUrl("1.9.1"))),
    path.join("home", "npm", "cache", "iterate_harness-1.9.1-py3-none-any.whl")
  );
});

test("pipInstallArgs pins upgrade, force-reinstall and the target", () => {
  assert.deepEqual(pipInstallArgs("https://example.com/x.tar.gz"), [
    "-m",
    "pip",
    "install",
    "--upgrade",
    "--force-reinstall",
    "https://example.com/x.tar.gz",
  ]);
});

test("MAX_DOWNLOAD_REDIRECTS stays at 5 (GitHub archive needs exactly 1)", () => {
  assert.equal(MAX_DOWNLOAD_REDIRECTS, 5);
});

test("installHarness succeeds on the first pip attempt without downloading", async () => {
  const pipCalls = [];
  const downloads = [];
  await installHarness({
    python: "/venv/bin/python",
    candidates: [
      "https://github.com/jingzhao-l/iterate-harness/archive/refs/tags/v1.9.1.tar.gz",
    ],
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn: (command, args) => pipCalls.push([command, args]),
    downloader: async (downloadUrl, dest) => downloads.push([downloadUrl, dest]),
  });
  assert.equal(pipCalls.length, 1);
  assert.deepEqual(
    pipCalls[0][1],
    pipInstallArgs("https://github.com/jingzhao-l/iterate-harness/archive/refs/tags/v1.9.1.tar.gz")
  );
  assert.equal(downloads.length, 0);
});

test("installHarness falls back to a local pip install after a TLS failure", async () => {
  const url = "https://github.com/jingzhao-l/iterate-harness/archive/refs/tags/v1.9.1.tar.gz";
  const cache = downloadCachePath(path.join("home", "npm"), "1.9.1");
  const pipCalls = [];
  const downloads = [];
  const runStepFn = (command, args) => {
    pipCalls.push([command, args]);
    if (pipCalls.length === 1) {
      throw new BootstrapError("pip exited with code 1: CERTIFICATE_VERIFY_FAILED");
    }
  };
  await installHarness({
    python: "/venv/bin/python",
    candidates: [url],
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn,
    downloader: async (downloadUrl, dest) => downloads.push([downloadUrl, dest]),
  });
  assert.equal(pipCalls.length, 2);
  assert.deepEqual(pipCalls[1][1], pipInstallArgs(cache));
  assert.deepEqual(downloads, [[url, cache]]);
});

test("installHarness wraps both failures when the download fallback dies too", async () => {
  const pipCalls = [];
  const runStepFn = (command, args) => {
    pipCalls.push([command, args]);
    throw new BootstrapError("pip exited with code 1: CERTIFICATE_VERIFY_FAILED");
  };
  await assert.rejects(
    installHarness({
      python: "/venv/bin/python",
      candidates: ["https://github.com/x/y.tar.gz"],
      homeDir: path.join("home", "npm"),
      version: "1.9.1",
      runStepFn,
      downloader: async () => {
        throw new Error("socket hang up");
      },
    }),
    (error) =>
      error instanceof BootstrapError &&
      error.message.includes("all install candidates failed") &&
      error.message.includes("socket hang up")
  );
  assert.equal(pipCalls.length, 1);
});

test("installHarness rethrows the primary error verbatim for a single non-http target", async () => {
  const primary = new BootstrapError("pip exited with code 1: build failed");
  await assert.rejects(
    installHarness({
      python: "/venv/bin/python",
      candidates: ["/tmp/iterate-harness-1.9.1.tar.gz"],
      homeDir: path.join("home", "npm"),
      version: "1.9.1",
      runStepFn: () => {
        throw primary;
      },
      downloader: async () => {
        throw new Error("should not download");
      },
    }),
    (error) => error instanceof BootstrapError && error.message.includes("build failed")
  );
});

test("installHarness tries the next candidate when the previous one fails", async () => {
  const pipCalls = [];
  const candidates = [pypiInstallSpec("1.9.1"), "https://github.com/x/y.whl"];
  await installHarness({
    python: "/venv/bin/python",
    candidates,
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn: (command, args) => {
      pipCalls.push([command, args]);
      if (pipCalls.length === 1) {
        // PyPI spec unavailable → fall through to the wheel.
        throw new BootstrapError("pip exited with code 1: No matching distribution");
      }
    },
    downloader: async (downloadUrl, dest) => undefined,
  });
  assert.equal(pipCalls.length, 2);
  assert.deepEqual(
    pipCalls.map(([, args]) => args),
    [pipInstallArgs(candidates[0]), pipInstallArgs(candidates[1])]
  );
});

test("installHarness propagates the second pip failure untouched", async () => {
  const cacheFailure = new BootstrapError("pip exited with code 1: no matching distribution");
  let pipCalls = 0;
  await assert.rejects(
    installHarness({
      python: "/venv/bin/python",
      candidates: ["https://github.com/x/y.tar.gz"],
      homeDir: path.join("home", "npm"),
      version: "1.9.1",
      runStepFn: () => {
        pipCalls += 1;
        if (pipCalls === 1) {
          throw new BootstrapError("pip exited with code 1: CERTIFICATE_VERIFY_FAILED");
        }
        throw cacheFailure;
      },
      downloader: async () => undefined,
    }),
    (error) =>
      error instanceof BootstrapError &&
      error.message.includes("no matching distribution")
  );
  assert.equal(pipCalls, 2);
});

// ---------------------------------------------------------------------------
// PyPI-first install (official index first, GitHub wheel, archive last)
// ---------------------------------------------------------------------------

test("installHarness downloads a wheel to a .whl cache file on a TLS failure", async () => {
  const wheelUrl =
    "https://github.com/jingzhao-l/iterate-harness/releases/download/v1.9.1/iterate_harness-1.9.1-py3-none-any.whl";
  const cache = downloadCachePath(path.join("home", "npm"), "1.9.1", ".whl");
  const pipCalls = [];
  const downloads = [];
  await installHarness({
    python: "/venv/bin/python",
    candidates: [wheelUrl],
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn: (command, args) => {
      pipCalls.push([command, args]);
      if (pipCalls.length === 1) {
        throw new BootstrapError("pip exited with code 1: CERTIFICATE_VERIFY_FAILED");
      }
    },
    downloader: async (downloadUrl, dest) => downloads.push([downloadUrl, dest]),
  });
  assert.equal(pipCalls.length, 2);
  assert.deepEqual(pipCalls[1][1], pipInstallArgs(cache));
  assert.deepEqual(downloads, [[wheelUrl, cache]]);
});

test("installHarness walks PyPI → wheel → archive across the fallback chain", async () => {
  const pypiSpec = pypiInstallSpec("1.9.1");
  const wheelUrl =
    "https://github.com/jingzhao-l/iterate-harness/releases/download/v1.9.1/iterate_harness-1.9.1-py3-none-any.whl";
  const archiveUrl = releaseTarballUrl("1.9.1");
  const pipArgs = [];
  const downloads = [];
  await installHarness({
    python: "/venv/bin/python",
    candidates: [pypiSpec, wheelUrl, archiveUrl],
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn: (command, args) => {
      pipArgs.push(args);
      throw new BootstrapError("pip exited with code 1: CERTIFICATE_VERIFY_FAILED");
    },
    downloader: async (downloadUrl, dest) => downloads.push([downloadUrl, dest]),
  }).catch(() => undefined);
  // The PyPI spec is non-URL: one pip attempt, no download.
  // The wheel (HTTP) and archive (HTTP) each do: pip → download → local pip.
  assert.deepEqual(pipArgs, [
    pipInstallArgs(pypiSpec),
    pipInstallArgs(wheelUrl),
    pipInstallArgs(downloadCachePath(path.join("home", "npm"), "1.9.1", ".whl")),
    pipInstallArgs(archiveUrl),
    pipInstallArgs(downloadCachePath(path.join("home", "npm"), "1.9.1")),
  ]);
  assert.deepEqual(downloads, [
    [wheelUrl, downloadCachePath(path.join("home", "npm"), "1.9.1", ".whl")],
    [archiveUrl, downloadCachePath(path.join("home", "npm"), "1.9.1")],
  ]);
});

test("installHarness surfaces the combined error when every candidate fails", async () => {
  const pypiSpec = pypiInstallSpec("1.9.1");
  const wheelUrl =
    "https://github.com/jingzhao-l/iterate-harness/releases/download/v1.9.1/iterate_harness-1.9.1-py3-none-any.whl";
  await assert.rejects(
    installHarness({
      python: "/venv/bin/python",
      candidates: [pypiSpec, wheelUrl],
      homeDir: path.join("home", "npm"),
      version: "1.9.1",
      runStepFn: () => {
        throw new BootstrapError("pip exited with code 1");
      },
      downloader: async () => {
        throw new BootstrapError("wheel unreachable");
      },
    }),
    (error) =>
      error instanceof BootstrapError && error.message.includes("all install candidates failed")
  );
});

test("installHarness installs a single explicit override directly", async () => {
  const url = "git+https://github.com/jingzhao-l/iterate-harness.git@main";
  const pipCalls = [];
  await installHarness({
    python: "/venv/bin/python",
    candidates: [url],
    homeDir: path.join("home", "npm"),
    version: "1.9.1",
    runStepFn: (command, args) => pipCalls.push([command, args]),
    downloader: async () => {
      throw new Error("should not download for a non-http url");
    },
  });
  assert.equal(pipCalls.length, 1);
  assert.deepEqual(pipCalls[0][1], pipInstallArgs(url));
});

test("installHarness rejects when no candidates are provided", async () => {
  await assert.rejects(
    installHarness({
      python: "/venv/bin/python",
      candidates: [],
      homeDir: path.join("home", "npm"),
      version: "1.9.1",
      runStepFn: () => undefined,
    }),
    (error) =>
      error instanceof BootstrapError && error.message.includes("no install candidate")
  );
});

test("downloadFile rejects immediately when the redirect budget is exhausted", async () => {
  await assert.rejects(
    downloadFile("https://github.com/x/y.tar.gz", "/tmp/never-written.tar.gz", -1),
    (error) =>
      error instanceof BootstrapError && error.message.includes("too many redirects")
  );
});

test("curlDownload invokes curl with fail-fast, redirects, timeout and output pinning", () => {
  const calls = [];
  const spawnFn = (command, args) => {
    calls.push([command, args]);
    return { status: 0, stdout: "", stderr: "" };
  };
  const outcome = curlDownload("https://github.com/x/y.tar.gz", "/tmp/y.tar.gz", spawnFn);
  assert.equal(outcome.ok, true);
  assert.equal(outcome.error, undefined);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], "curl");
  assert.deepEqual(calls[0][1], [
    "-fsSL",
    "--max-time",
    "240",
    "-o",
    "/tmp/y.tar.gz",
    "https://github.com/x/y.tar.gz",
  ]);
});

test("curlDownload reports non-zero exits and spawn errors as BootstrapError", () => {
  const exitOutcome = curlDownload("https://x/y.tar.gz", "/tmp/y.tar.gz", () => ({
    status: 22,
    stdout: "",
    stderr: "curl: (22) The requested URL returned error: 404",
  }));
  assert.equal(exitOutcome.ok, false);
  assert.ok(exitOutcome.error.message.includes("404"));

  const errorOutcome = curlDownload("https://x/y.tar.gz", "/tmp/y.tar.gz", () => ({
    status: null,
    error: new Error("spawn curl ENOENT"),
    stdout: "",
    stderr: "",
  }));
  assert.equal(errorOutcome.ok, false);
  assert.ok(errorOutcome.error.message.includes("ENOENT"));
});

test("downloadTarballTo tries node-https first and skips curl on success", async () => {
  const curlCalls = [];
  const dest = await downloadTarballTo("https://x/y.tar.gz", "/tmp/never-curl.tar.gz", {
    nodeDownload: async () => "/tmp/never-curl.tar.gz",
    curlDownload: (...args) => {
      curlCalls.push(args);
      return { ok: true, error: undefined };
    },
  });
  assert.equal(dest, "/tmp/never-curl.tar.gz");
  assert.equal(curlCalls.length, 0);
});

test("downloadTarballTo falls back to curl when node-https fails", async () => {
  const nodeCalls = [];
  const curlCalls = [];
  await downloadTarballTo("https://x/y.tar.gz", "/tmp/fallback.tar.gz", {
    nodeDownload: async (url) => {
      nodeCalls.push(url);
      throw new Error("unable to verify the first certificate");
    },
    curlDownload: (url, dest) => {
      curlCalls.push([url, dest]);
      return { ok: true, error: undefined };
    },
  });
  assert.deepEqual(nodeCalls, ["https://x/y.tar.gz"]);
  assert.deepEqual(curlCalls, [["https://x/y.tar.gz", "/tmp/fallback.tar.gz"]]);
});

test("downloadTarballTo aggregates both download failures", async () => {
  await assert.rejects(
    downloadTarballTo("https://x/y.tar.gz", "/tmp/both-fail.tar.gz", {
      nodeDownload: async () => {
        throw new Error("node TLS broke");
      },
      curlDownload: () => ({
        ok: false,
        error: new BootstrapError("curl download of https://x/y.tar.gz failed: exit code 6"),
      }),
    }),
    (error) =>
      error instanceof BootstrapError &&
      error.message.includes("node-https: node TLS broke") &&
      error.message.includes("curl: curl download of https://x/y.tar.gz failed: exit code 6")
  );
});
