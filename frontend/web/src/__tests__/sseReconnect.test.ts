// SSE reconnect policy tests (design §17.4 / §18.4).
//
// EventSource exposes no HTTP status for a failed connection, so a rejected
// access token is indistinguishable from a network blip — the browser retries
// the same bad URL forever while the sidebar shows a bare "重连中…". The store
// therefore probes the REST API (which *can* send the Authorization header)
// once per failure burst and parks in a terminal "unauthorized" state when the
// token is the problem, instead of retrying (and toast-spamming) pointlessly.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { subscribeToStatus, useWebUi } from "../store";
import { webuiToken } from "../api";

vi.mock("../api", () => ({
  api: {
    status: vi.fn(async () => ({})),
    chatStatus: vi.fn(async () => null),
    chatHistory: vi.fn(async () => []),
  },
  webuiToken: vi.fn(() => ""),
}));

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readonly listeners = new Map<string, (event: Event) => void>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, handler: (event: Event) => void): void {
    this.listeners.set(type, handler);
  }

  close(): void {
    this.closed = true;
  }
}

async function flush(): Promise<void> {
  // Let the auth probe's promise chain settle without moving the clock.
  await vi.advanceTimersByTimeAsync(0);
}

describe("SSE reconnect policy", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
    useWebUi.setState({ toasts: [], connectionState: "connecting" });
    vi.mocked(webuiToken).mockReturnValue("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("stops retrying and names the fix when the token is rejected", async () => {
    vi.mocked(webuiToken).mockReturnValue("stale-token");
    const fetchMock = vi.fn(async () => ({ status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const cleanup = subscribeToStatus("");
    expect(FakeEventSource.instances).toHaveLength(1);

    FakeEventSource.instances[0].onerror?.();
    await flush();

    expect(useWebUi.getState().connectionState).toBe("unauthorized");
    expect(useWebUi.getState().toasts.map((t) => t.message).join(" ")).toContain("令牌");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/status",
      expect.objectContaining({ headers: { Authorization: "Bearer stale-token" } }),
    );

    // No retry storm: however long we wait, the dead URL is not re-opened.
    await vi.advanceTimersByTimeAsync(30_000);
    expect(FakeEventSource.instances).toHaveLength(1);
    cleanup();
  });

  it("treats 403 the same as 401", async () => {
    vi.mocked(webuiToken).mockReturnValue("token");
    vi.stubGlobal("fetch", vi.fn(async () => ({ status: 403 })));

    const cleanup = subscribeToStatus("");
    FakeEventSource.instances[0].onerror?.();
    await flush();

    expect(useWebUi.getState().connectionState).toBe("unauthorized");
    cleanup();
  });

  it("keeps retrying when the failure was not an auth failure", async () => {
    vi.mocked(webuiToken).mockReturnValue("good-token");
    const fetchMock = vi.fn(async () => ({ status: 500 }));
    vi.stubGlobal("fetch", fetchMock);

    const cleanup = subscribeToStatus("");
    FakeEventSource.instances[0].onerror?.();
    await flush();

    expect(useWebUi.getState().connectionState).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(3_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(useWebUi.getState().connectionState).not.toBe("unauthorized");
    cleanup();
  });

  it("keeps retrying when the probe itself fails (offline)", async () => {
    vi.mocked(webuiToken).mockReturnValue("good-token");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      }),
    );

    const cleanup = subscribeToStatus("");
    FakeEventSource.instances[0].onerror?.();
    await flush();

    expect(useWebUi.getState().connectionState).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(3_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    cleanup();
  });

  it("does not probe at all when auth is disabled", async () => {
    // No token configured server-side: a 401 is impossible, so the probe would
    // be a pointless extra request on every blip.
    vi.mocked(webuiToken).mockReturnValue("");
    const fetchMock = vi.fn(async () => ({ status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    const cleanup = subscribeToStatus("");
    FakeEventSource.instances[0].onerror?.();
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(useWebUi.getState().connectionState).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(3_000);
    expect(FakeEventSource.instances).toHaveLength(2);
    cleanup();
  });

  it("carries the token on the events URL (the one route that accepts ?token=)", () => {
    vi.mocked(webuiToken).mockReturnValue("tok en");
    const cleanup = subscribeToStatus("/proj");
    const url = FakeEventSource.instances[0].url;
    expect(url).toContain("/api/v1/events");
    expect(url).toContain("token=tok%20en");
    expect(url).toContain("project_root=%2Fproj");
    cleanup();
  });

  it("resyncs history after a successful reconnect", async () => {
    const { api } = await import("../api");
    const cleanup = subscribeToStatus("");
    const first = FakeEventSource.instances[0];
    first.onopen?.();
    first.onerror?.();
    await vi.advanceTimersByTimeAsync(3_000);

    const second = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    second.onopen?.();
    await flush();

    expect(useWebUi.getState().connectionState).toBe("connected");
    expect(FakeEventSource.instances.length).toBeGreaterThan(1);
    // Events published while the stream was down are gone from the hub, so a
    // reconnect must rebuild the transcript from REST (this is also what keeps
    // persisted tool-activity cards alive across a blip).
    expect(api.chatHistory).toHaveBeenCalled();
    cleanup();
  });

  it("stops everything on unsubscribe", async () => {
    vi.mocked(webuiToken).mockReturnValue("tok");
    const cleanup = subscribeToStatus("");
    const instance = FakeEventSource.instances[0];
    cleanup();
    expect(instance.closed).toBe(true);
    expect(useWebUi.getState().connectionState).toBe("disconnected");

    // A late error from the dead socket must not resurrect the retry loop.
    instance.onerror?.();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});