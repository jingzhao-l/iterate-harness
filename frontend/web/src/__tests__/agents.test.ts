// Tests for the team-roster pure helpers (src/agents.ts, design §17.3 P9).
// These are the rules the console displays with; a wrong one is a wrong
// "which worker is stuck" answer, so each is pinned down explicitly.

import { describe, expect, it } from "vitest";

import {
  STATUS_FILTERS,
  filterAgents,
  formatBytes,
  formatDuration,
  kindLabel,
  lastLine,
  primaryId,
  rosterKpis,
  statusLabel,
  type StatusFilter,
} from "../agents";
import type { AgentRow } from "../types";

let nextId = 0;

function agent(overrides: Partial<AgentRow> = {}): AgentRow {
  nextId += 1;
  return {
    id: `task-${nextId}`,
    agent_id: "",
    team: "",
    kind: "subprocess",
    backend: "subprocess",
    task_type: "local_agent",
    status: "running",
    running: true,
    description: "",
    status_note: "",
    progress: null,
    started_at: null,
    ended_at: null,
    duration_s: 0,
    return_code: null,
    output_bytes: 0,
    tail: "",
    cwd: "",
    ...overrides,
  };
}

describe("rosterKpis", () => {
  it("counts each status bucket independently from the running flag", () => {
    const agents = [
      agent({ status: "running", running: true }),
      agent({ status: "running", running: true }),
      agent({ status: "failed", running: false }),
      agent({ status: "killed", running: false }),
      agent({ status: "completed", running: false }),
      agent({ status: "pending", running: false }),
    ];
    expect(rosterKpis(agents)).toEqual({
      running: 2,
      failed: 1,
      killed: 1,
      completed: 1,
      total: 6,
    });
  });

  it("returns all zeros for an empty roster", () => {
    expect(rosterKpis([])).toEqual({
      running: 0,
      failed: 0,
      killed: 0,
      completed: 0,
      total: 0,
    });
  });

  it("does not double count a killed agent as completed", () => {
    const kpis = rosterKpis([agent({ status: "killed", running: false })]);
    expect(kpis.killed).toBe(1);
    expect(kpis.completed).toBe(0);
    expect(kpis.failed).toBe(0);
  });
});

describe("formatDuration", () => {
  it("shows 刚刚 for a fresh spawn instead of 0s", () => {
    expect(formatDuration(0)).toBe("刚刚");
    expect(formatDuration(0.4)).toBe("刚刚");
  });

  it("renders sub-minute durations as whole seconds", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(59.6)).toBe("60s");
  });

  it("renders minute and hour buckets", () => {
    expect(formatDuration(125)).toBe("2m5s");
    expect(formatDuration(3600)).toBe("1h0m");
    expect(formatDuration(7200 + 900)).toBe("2h15m");
  });

  it("renders days past the 24h mark", () => {
    expect(formatDuration(25 * 3600)).toBe("1d1h");
    expect(formatDuration(3 * 86400 + 4 * 3600)).toBe("3d4h");
  });

  it("degrades invalid input to 刚刚 rather than NaN", () => {
    expect(formatDuration(undefined)).toBe("刚刚");
    expect(formatDuration(null)).toBe("刚刚");
    expect(formatDuration(-20)).toBe("刚刚");
  });
});

describe("formatBytes", () => {
  it("uses B, KB, MB at the right thresholds", () => {
    expect(formatBytes(0)).toBe("0B");
    expect(formatBytes(512)).toBe("512B");
    expect(formatBytes(1024)).toBe("1.0KB");
    expect(formatBytes(153600)).toBe("150.0KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0MB");
  });

  it("never shows a negative size", () => {
    expect(formatBytes(-1)).toBe("0B");
  });

  it("degrades missing sizes to 0B", () => {
    expect(formatBytes(undefined)).toBe("0B");
    expect(formatBytes(null)).toBe("0B");
  });
});

describe("statusLabel / kindLabel", () => {
  it("maps every known status to a Chinese label", () => {
    expect(statusLabel("running")).toBe("在跑");
    expect(statusLabel("pending")).toBe("排队");
    expect(statusLabel("completed")).toBe("完成");
    expect(statusLabel("failed")).toBe("失败");
    expect(statusLabel("killed")).toBe("已停");
  });

  it("labels an unknown status as 未知 instead of crashing", () => {
    expect(statusLabel("exploding")).toBe("未知");
    expect(statusLabel("")).toBe("未知");
    expect(statusLabel(null)).toBe("未知");
    expect(statusLabel(undefined)).toBe("未知");
  });

  it("maps every known kind to a Chinese label", () => {
    expect(kindLabel("in_process")).toBe("进程内");
    expect(kindLabel("subprocess")).toBe("子进程");
    expect(kindLabel("shell")).toBe("Shell");
    expect(kindLabel("other")).toBe("其他");
  });

  it("keeps an unknown kind visible instead of hiding it", () => {
    expect(kindLabel("remote_team")).toBe("remote_team");
    expect(kindLabel("")).toBe("其他");
    expect(kindLabel(undefined)).toBe("其他");
  });
});

describe("primaryId", () => {
  it("prefers the swarm identity when one exists", () => {
    expect(primaryId(agent({ agent_id: "reviewer@team1" }))).toBe("reviewer@team1");
  });

  it("falls back to the task id for a plain shell task", () => {
    expect(primaryId(agent({ id: "task-7", agent_id: "" }))).toBe("task-7");
  });

  it("falls back when the swarm identity is whitespace only", () => {
    expect(primaryId(agent({ id: "task-9", agent_id: "   " }))).toBe("task-9");
  });

  it("trims the identity it displays", () => {
    expect(primaryId(agent({ agent_id: "  echo@team  " }))).toBe("echo@team");
  });
});

describe("filterAgents", () => {
  const agents = [
    agent({ id: "task-a", agent_id: "reviewer@team1", team: "team1", status: "running", running: true }),
    agent({
      id: "task-b",
      agent_id: "",
      kind: "shell",
      task_type: "local_bash",
      team: "",
      status: "failed",
      running: false,
      description: "lint the tests",
    }),
    agent({
      id: "task-c",
      agent_id: "builder@team2",
      team: "team2",
      task_type: "local_bash",
      status: "completed",
      running: false,
      status_note: "emitting wheels",
    }),
  ];

  it("returns every row for the 全部 filter, including non-running ones", () => {
    expect(filterAgents(agents, { status: "all" }).map((a) => a.id)).toEqual([
      "task-a",
      "task-b",
      "task-c",
    ]);
  });

  it("matches 在跑 on the running flag, not the status literal", () => {
    expect(filterAgents(agents, { status: "running" }).map((a) => a.id)).toEqual(["task-a"]);
  });

  it("matches the terminal filters on status", () => {
    expect(filterAgents(agents, { status: "failed" }).map((a) => a.id)).toEqual(["task-b"]);
    expect(filterAgents(agents, { status: "completed" }).map((a) => a.id)).toEqual(["task-c"]);
    expect(filterAgents(agents, { status: "killed" })).toEqual([]);
  });

  it("searches across identity, team, description, note and cwd case-insensitively", () => {
    expect(filterAgents(agents, { query: "REVIEWER" }).map((a) => a.id)).toEqual(["task-a"]);
    expect(filterAgents(agents, { query: "team2" }).map((a) => a.id)).toEqual(["task-c"]);
    expect(filterAgents(agents, { query: "lint the tests" }).map((a) => a.id)).toEqual(["task-b"]);
    expect(filterAgents(agents, { query: "emitting" }).map((a) => a.id)).toEqual(["task-c"]);
    expect(filterAgents(agents, { query: "task-b" }).map((a) => a.id)).toEqual(["task-b"]);
    expect(filterAgents(agents, { query: "local_agent" }).map((a) => a.id)).toEqual(["task-a"]);
  });

  it("combines a status filter with a search query", () => {
    expect(filterAgents(agents, { status: "running", query: "team1" }).map((a) => a.id)).toEqual([
      "task-a",
    ]);
    expect(filterAgents(agents, { status: "running", query: "team2" })).toEqual([]);
  });

  it("ignores a blank or whitespace-only query", () => {
    expect(filterAgents(agents, { query: "" }).length).toBe(3);
    expect(filterAgents(agents, { query: "   " }).length).toBe(3);
  });

  it("reports an empty result rather than throwing for a no-match query", () => {
    expect(filterAgents(agents, { query: "does-not-exist" })).toEqual([]);
  });

  it("treats a missing options object as unfiltered", () => {
    expect(filterAgents(agents, {}).length).toBe(3);
  });
});

describe("lastLine", () => {
  it("returns the last non-empty line", () => {
    expect(lastLine("checking tests\nlint passed\n")).toBe("lint passed");
    expect(lastLine("\n\n  indented line  \n")).toBe("indented line");
  });

  it("handles windows line endings", () => {
    expect(lastLine("a\r\nb\r\nc\r\n")).toBe("c");
  });

  it("returns an empty string when there is nothing to show", () => {
    expect(lastLine("")).toBe("");
    expect(lastLine("   \n\n  ")).toBe("");
    expect(lastLine(null)).toBe("");
    expect(lastLine(undefined)).toBe("");
  });
});

describe("STATUS_FILTERS", () => {
  it("offers one entry per status the backend can filter by", () => {
    const values: StatusFilter[] = STATUS_FILTERS.map((entry) => entry.value);
    expect(values).toContain("all");
    expect(values).toContain("running");
    expect(values).toContain("failed");
    expect(values).toContain("killed");
    expect(values).toContain("completed");
  });

  it("does not duplicate a filter", () => {
    const values = STATUS_FILTERS.map((entry) => entry.value);
    expect(new Set(values).size).toBe(values.length);
  });

  it("gives every filter a non-empty label", () => {
    for (const entry of STATUS_FILTERS) {
      expect(entry.label.length).toBeGreaterThan(0);
    }
  });
});
