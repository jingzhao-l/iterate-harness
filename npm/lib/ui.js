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

function askYesNo(question, defaultNo = false) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    const hint = defaultNo ? "[y/N]" : "[Y/n]";
    rl.question(`\x1b[36m◆\x1b[0m  ${question} ${hint} `, (answer) => {
      rl.close();
      const a = answer.trim().toLowerCase();
      if (a === "y" || a === "yes") resolve(true);
      else if (a === "n" || a === "no") resolve(false);
      else resolve(defaultNo);
    });
  });
}

module.exports = {
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