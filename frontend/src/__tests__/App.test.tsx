import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App, { LOAD_RETRIES } from "../App";

const AGENTS = [
  { id: "A11", name: "Credit Card Recommendation", description: "", keywords: [], tools: ["get_customer_profile", "recommend_cards"] },
  { id: "A12", name: "Transaction Queries", description: "", keywords: [], tools: ["search_transactions", "get_dispute_status"] },
  { id: "A13", name: "Policy Knowledge Base", description: "", keywords: [], tools: ["search_policies"] },
  { id: "A14", name: "Personal Finance (PFM)", description: "", keywords: [], tools: ["spending_summary", "budget_status"] },
];

const ANSWER = {
  status: "ok",
  response: "$35 late fee [POL-LATE-03]. You spent $188.70 on dining.",
  context: {
    agents: [{ agent_id: "A13" }, { agent_id: "A14" }],
    tools_used: ["search_policies", "spending_summary"],
    llm_calls: 6,
    cost_usd: 0,
    latency_ms: 12,
  },
};

type Call = { url: string; body?: Record<string, unknown> };
let calls: Call[];

function stubBackend(health: Record<string, unknown> = { status: "ok", llm: "mock", agents: ["A11", "A12", "A13", "A14"] }) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ url, body });
      const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
      if (url === "/health") return json(health);
      if (url === "/v1/agents") return json(AGENTS);
      if (url === "/v1/chat/manual") return json({ ...ANSWER, session_id: "sess_before", trace_id: "trace_b", path: "manual" });
      if (url === "/v1/chat") return json({ ...ANSWER, session_id: "sess_after", trace_id: "trace_a", path: "full" });
      return new Response("not found", { status: 404 });
    }),
  );
}

beforeEach(() => stubBackend());
afterEach(() => vi.unstubAllGlobals());

describe("App", () => {
  it("shows the model and the agent catalog from the backend", async () => {
    render(<App />);
    expect(await screen.findByText("Offline mock (free)")).toBeInTheDocument();
    expect(await screen.findByText("Credit Card Recommendation")).toBeInTheDocument();
    expect(screen.getByText("search_transactions, get_dispute_status")).toBeInTheDocument();
  });

  it("shows Claude and the budget when the backend runs on Claude", async () => {
    stubBackend({ status: "ok", llm: "anthropic", agents: [], budget_remaining_usd: 4.9876 });
    render(<App />);
    expect(await screen.findByText("Claude")).toBeInTheDocument();
    expect(screen.getByTestId("budget")).toHaveTextContent("$4.99");
  });

  it("asks both sides and shows a green match strip when the selection is right", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    // tick A14 first, then A13: tick order must not matter
    await user.click(screen.getByRole("checkbox", { name: /A14/ }));
    await user.click(screen.getByRole("checkbox", { name: /A13/ }));
    await user.click(screen.getByRole("button", { name: "Two agents, one tool each" }));
    await user.click(screen.getByRole("button", { name: "Ask both" }));

    const strip = await screen.findByTestId("comparison");
    expect(strip).toHaveClass("same");
    expect(strip).toHaveTextContent("Both sides match");

    const manual = calls.find((c) => c.url === "/v1/chat/manual")!;
    expect(manual.body).toMatchObject({
      customer_id: "C1001",
      message: "What is the late payment fee, and how much did I spend on dining this month?",
      agent_ids: ["A13", "A14"],
      session_id: null,
    });
    expect(calls.find((c) => c.url === "/v1/chat")!.body).not.toHaveProperty("agent_ids");

    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/v1/audit/trace_b", "/v1/audit/trace_a"]);
  });

  it("reuses each side's session on the next question", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.click(screen.getByRole("checkbox", { name: /A13/ }));
    const box = screen.getByRole("textbox", { name: "Your question" });
    await user.type(box, "first{Enter}");
    await screen.findByTestId("comparison");
    await user.clear(box);
    await user.type(box, "second{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.url === "/v1/chat")).toHaveLength(2));
    const [, secondAfter] = calls.filter((c) => c.url === "/v1/chat");
    const [, secondBefore] = calls.filter((c) => c.url === "/v1/chat/manual");
    expect(secondAfter.body?.session_id).toBe("sess_after");
    expect(secondBefore.body?.session_id).toBe("sess_before");
  });

  it("doesn't send the left side when no agent is ticked", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "What about my card?");
    await user.click(screen.getByRole("button", { name: "Ask both" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/v1/chat")).toBe(true));
    expect(calls.some((c) => c.url === "/v1/chat/manual")).toBe(false);
    expect(screen.getByTestId("hint")).toHaveTextContent("This is the step the new way removes");
    expect(screen.queryByTestId("comparison")).not.toBeInTheDocument();
  });

  it("Shift+Enter adds a line instead of sending", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "line one{Shift>}{Enter}{/Shift}line two");
    expect(screen.getByRole("textbox", { name: "Your question" })).toHaveValue("line one\nline two");
    expect(calls.some((c) => c.url === "/v1/chat")).toBe(false);
  });

  it("Start over clears both threads and the strip", async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.click(screen.getByRole("checkbox", { name: /A13/ }));
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "late fee{Enter}");
    await screen.findByTestId("comparison");
    await user.click(screen.getByRole("button", { name: "Start over" }));
    expect(screen.queryByTestId("comparison")).not.toBeInTheDocument();
    expect(screen.getByText("The combined answer appears here.")).toBeInTheDocument();
  });

  it("escapes server text instead of rendering it as HTML", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const json = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
        if (url === "/health") return json({ status: "ok", llm: "mock", agents: [] });
        if (url === "/v1/agents") return json(AGENTS);
        return json({ ...ANSWER, response: "<img src=x onerror=alert(1)>", session_id: "s", trace_id: "t", path: "full" });
      }),
    );
    const { container } = render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "hi{Enter}");
    const after = await screen.findAllByText("<img src=x onerror=alert(1)>");
    expect(after.length).toBeGreaterThan(0);
    expect(container.querySelector("img")).toBeNull();
  });

  it("retries while the backend starts, then says when it isn't reachable", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = vi.fn(async (_url: string) => { throw new TypeError("Failed to fetch"); });
      vi.stubGlobal("fetch", fetchMock);
      render(<App />);
      expect(screen.getByText("connecting…")).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync((LOAD_RETRIES + 1) * 1000));
      expect(screen.getByText("server not reachable")).toBeInTheDocument();
      expect(fetchMock.mock.calls.filter(([u]) => u === "/health")).toHaveLength(LOAD_RETRIES + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("recovers when the backend comes up a moment after the page", async () => {
    vi.useFakeTimers();
    try {
      let up = false;
      vi.stubGlobal("fetch", vi.fn(async (url: string) => {
        if (!up) throw new TypeError("Failed to fetch");
        const json = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
        return url === "/health" ? json({ status: "ok", llm: "mock", agents: [] }) : json(AGENTS);
      }));
      render(<App />);
      await act(() => vi.advanceTimersByTimeAsync(2000));
      up = true;
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(screen.getByText("Offline mock (free)")).toBeInTheDocument();
      expect(screen.getByText("Transaction Queries")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a request error on the side that failed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const json = (d: unknown, status = 200) => new Response(JSON.stringify(d), { status });
        if (url === "/health") return json({ status: "ok", llm: "mock", agents: [] });
        if (url === "/v1/agents") return json(AGENTS);
        return json({ detail: "session belongs to another customer" }, 403);
      }),
    );
    render(<App />);
    await screen.findByText("Policy Knowledge Base");
    await user.type(screen.getByRole("textbox", { name: "Your question" }), "hi{Enter}");
    const thread = await screen.findByText(/Request failed: session belongs to another customer/);
    expect(within(thread.closest("article")!).getByText("Not answered")).toBeInTheDocument();
  });
});
