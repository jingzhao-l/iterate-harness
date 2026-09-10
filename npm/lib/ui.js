"use strict";

/*
 * Terminal UI helpers for the iterate-harness npm wrapper.
 *
 * Mirrors the style of iterate-skill-installer's interactive installer:
 *   - ASCII banner (ITERATE brand)
 *   - Colored step/success/warning/error/info prefixes
 *   - Framed section box for summary
 *   - Yes/no prompt for interactive choices
 *
 * Every function writes to stderr so piped stdout (e.g. `ih --version | jq`)
 * is never polluted with UI noise. Non-TTY stderr silently skips the banner.
 */

const readline = require("node:readline");

const ITERATE_BANNER = [
  "██╗████████╗███████╗██████╗  █████╗ ████████╗███████╗",
  "██║╚══██╔══╝██╔════╝██╔══██╗██╔══██╗╚══██╔══╝██╔════╝",
  "██║   ██║   █████╗  ██████╔╝███████║   ██║   █████╗  ",
  "██║   ██║   ██╔══╝  ██╔══██╗██╔══██║   ██║   ██╔══╝  ",
  "██║   ██║   ███████╗██║  ██║██║  ██║   ██║   ███████╗",
  "╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   ╚══════╝",
];

function printBanner() {
  if (!process.stderr.isTTY) return;
  console.error();
  for (const line of ITERATE_BANNER) {
    console.error(`\x1b[36m${line}\x1b[0m`);
  }
  console.error(`\x1b[36m  iterate-harness\x1b[0m\x1b[2m · jingzhao-l/iterate-harness\x1b[0m`);
  console.error();
}

function info(message) {
  console.error(`\x1b[34mℹ\x1b[0m  ${message}`);
}

function success(message) {
  console.error(`\x1b[32m✓\x1b[0m  ${message}`);
}

function warning(message) {
  console.error(`\x1b[33m⚠\x1b[0m  ${message}`);
}

function error(message) {
  console.error(`\x1b[31m✗\x1b[0m  ${message}`);
}

function step(message) {
  console.error(`\x1b[36m◆\x1b[0m  ${message}`);
}

function hint(message) {
  console.error(`\x1b[2m   ${message}\x1b[0m`);
}

function stripAnsi(str) {
  // eslint-disable-next-line no-control-regex
  return str.replace(/\x1b\[[0-9;]*m/g, "");
}

function frameSection(title, lines) {
  const maxLen = Math.max(
    title.length,
    ...lines.map((l) => stripAnsi(l).length),
  );
  const innerWidth = maxLen + 2;
  const top = `┌─ ${title} ${"─".repeat(Math.max(0, innerWidth - title.length - 2))}┐`;
  const bottom = `└${"─".repeat(innerWidth + 1)}┘`;
  console.error(top);
  for (const line of lines) {
    const visibleLen = stripAnsi(line).length;
    const padding = " ".repeat(Math.max(0, innerWidth - visibleLen));
    console.error(`│ ${line}${padding}│`);
  }
  console.error(bottom);
}

// Safety net for unattended runs: if stdin is closed (EOF) or the user never
// answers, fall back to the default instead of hanging forever. Override per
// call with `options.timeoutMs` (0 disables the timeout entirely).
const DEFAULT_PROMPT_TIMEOUT_MS = 120000;

function askYesNo(question, defaultNo = false, options = {}) {
  const input = options.input || process.stdin;
  const output = options.output || process.stdout;
  const timeoutMs =
    options.timeoutMs === undefined ? DEFAULT_PROMPT_TIMEOUT_MS : options.timeoutMs;
  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const rl = readline.createInterface({ input, output });
    const finish = (value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      rl.close();
      resolve(value);
    };
    // readline emits "close" on EOF (piped/`< /dev/null` stdin) — never hang.
    rl.on("close", () => finish(defaultNo));
    if (timeoutMs > 0) {
      timer = setTimeout(() => {
        process.stderr.write(
          `\x1b[2m   no response after ${Math.round(timeoutMs / 1000)}s; using default\x1b[0m\n`
        );
        finish(defaultNo);
      }, timeoutMs);
      if (typeof timer.unref === "function") timer.unref();
    }
    const hint = defaultNo ? "[y/N]" : "[Y/n]";
    rl.question(`\x1b[36m◆\x1b[0m  ${question} ${hint} `, (answer) => {
      const a = String(answer).trim().toLowerCase();
      if (a === "y" || a === "yes") finish(true);
      else if (a === "n" || a === "no") finish(false);
      else finish(defaultNo);
    });
  });
}

module.exports = {
  DEFAULT_PROMPT_TIMEOUT_MS,
  ITERATE_BANNER,
  printBanner,
  info,
  success,
  warning,
  error,
  step,
  hint,
  stripAnsi,
  frameSection,
  askYesNo,
};