import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const AGENTS = [
  { id: "A13", name: "Policy Knowledge Base", description: "", keywords: [], tools: ["search_policies"] },
  { id: "A14", name: "Personal Finance (PFM)", description: "", keywords: [], tools: ["spending_summary"] },
];
const BASE = {
  status: "ok",
  context: {
    agents: [{ agent_id: "A13" }, { agent_id: "A14" }],
    tools_used: ["search_policies", "spending_summary"],
  },
};
const FULL = {
  ...BASE, session_id: "sess_after", trace_id: "trace_full", path: "full", response: "First answer: $35 and $188.70",
  context: { ...BASE.context, llm_calls: 6, cost_usd: 0.0118, latency_ms: 5400,
             cache: { hit: false, reason: "no plan for this set of agents", saved: true, save_note: "saved" } },
};
const HIT = {
  ...BASE, session_id: "sess_after", trace_id: "trace_hit", path: "cache", response: "Cached answer: $35 and $188.70",
  context: { ...BASE.context, llm_calls: 2, cost_usd: 0.0041, latency_ms: 1200,
             cache: { hit: true, plan_id: "plan_1", similarity: 0.86, hits: 1,
                      first_run: { llm_calls: 6, latency_ms: 5400, cost_usd: 0.0118 } } },
};
const DETAIL = {
  ...BASE, session_id: "sess_after", trace_id: "trace_detail", path: "full", response: "Itemised answer",
  context: { ...BASE.context, llm_calls: 6, cost_usd: 0.02, latency_ms: 6000, detail: true,
             answered_question: "late fee and dining" },
};

let calls: { url: string; method: string; body?: Record<string, unknown> }[];
let plans = 0;

beforeEach(() => {
  calls = [];
  plans = 0;
  let chats = 0;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ url, method, body });
    const json = (d: unknown) => new Response(JSON.stringify(d), { status: 200 });
    if (url === "/health") return json({ status: "ok", llm: "anthropic", agents: [] });
    if (url === "/v1/agents") return json(AGENTS);
    if (url === "/v1/cache" && method === "DELETE") { const n = plans; plans = 0; return json({ cleared: n }); }
    if (url === "/v1/cache") return json({ enabled: true, count: plans, plans: [] });
    if (url === "/v1/chat/manual") return json({ ...FULL, session_id: "sess_before", trace_id: "trace_m", path: "manual" });
    if (url === "/v1/chat") {
      if (body?.more_details) return json(DETAIL);
      plans = 1;
      return json(chats++ === 0 ? FULL : HIT);
    }
    return new Response("{}", { status: 404 });
  }));
});
afterEach(() => vi.unstubAllGlobals());

async function askTwice(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByText("Policy Knowledge Base");
  await user.click(screen.getByRole("checkbox", { name: /A13/ }));
  await user.click(screen.getByRole("checkbox", { name: /A14/ }));
  const box = screen.getByRole("textbox", { name: "Your question" });
  await user.type(box, "late fee and dining{Enter}");
  await screen.findAllByText("First answer: $35 and $188.70");
  await user.type(box, "{Enter}");                      // ask the same question again
  await screen.findAllByText("Cached answer: $35 and $188.70");
}

describe("plan cache in the UI", () => {
  it("shows the cached re-run below the first answer, with calls, time and cost compared", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await askTwice(user);
    const after = container.querySelector(".after")!;
    const turns = after.querySelectorAll("article.turn");
    expect(turns).toHaveLength(2);
    expect(turns[0]).toHaveTextContent("First answer");          // oldest first
    expect(turns[1]).toHaveTextContent("Cached answer");
    expect(within(turns[0] as HTMLElement).getByText("Full flow")).toBeInTheDocument();
    expect(within(turns[0] as HTMLElement).getByTestId("cache-note")).toHaveTextContent("Plan saved");
    expect(within(turns[1] as HTMLElement).getByText("From cache")).toBeInTheDocument();
    const note = within(turns[1] as HTMLElement).getByTestId("cache-note");
    expect(note).toHaveTextContent("LLM calls: 2 instead of 6");
    expect(note).toHaveTextContent("Time: 1.2 s instead of 5.4 s");
    expect(note).toHaveTextContent("Cost: $0.0041 instead of $0.0118");
    expect(screen.getByTestId("comparison")).toHaveTextContent("(from cache)");
  });

  it("More details re-runs the previous question in full on the platform side only", async () => {
    const user = userEvent.setup();
    const { container } = render(<App />);
    await askTwice(user);
    await user.click(screen.getByRole("button", { name: "More details" }));
    await screen.findByText("Itemised answer");
    const req = calls.filter((c) => c.url === "/v1/chat").at(-1)!;
    expect(req.body).toMatchObject({ more_details: true, session_id: "sess_after" });
    expect(calls.filter((c) => c.url === "/v1/chat/manual")).toHaveLength(2);   // left side not re-asked
    const last = [...container.querySelectorAll(".after article.turn")].at(-1) as HTMLElement;
    expect(within(last).getByText("Detailed answer")).toBeInTheDocument();
    expect(within(last).getByText("More detail on: late fee and dining")).toBeInTheDocument();
  });

  it("shows the number of cached plans and can clear them", async () => {
    const user = userEvent.setup();
    render(<App />);
    await askTwice(user);
    await waitFor(() => expect(screen.getByTestId("plans")).toHaveTextContent("1"));
    await user.click(screen.getByRole("button", { name: "Clear" }));
    await waitFor(() => expect(screen.getByTestId("plans")).toHaveTextContent("0"));
    expect(calls.some((c) => c.url === "/v1/cache" && c.method === "DELETE")).toBe(true);
  });
});
