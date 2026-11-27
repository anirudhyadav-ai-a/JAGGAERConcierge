import type { ChatResponse } from "./api";

export const STATUS_LABELS: Record<string, { label: string; tone: string }> = {
  ok: { label: "Answered", tone: "ok" },
  clarify: { label: "Asked you to clarify", tone: "clarify" },
  out_of_scope: { label: "Outside what it can help with", tone: "clarify" },
  partial: { label: "Partly answered", tone: "partial" },
  error: { label: "Not answered", tone: "error" },
  budget_exceeded: { label: "Spending limit reached", tone: "budget" },
};

export function statusLabel(status: string): { label: string; tone: string } {
  return STATUS_LABELS[status] ?? { label: status, tone: "error" };
}

export interface Comparison {
  headline: string;
  match: boolean; // same agents (and the platform didn't ask to clarify): green strip, otherwise red
  sameAgents: boolean;
  sameTools: boolean;
  sameAnswer: boolean;
  leftAgents: string[];
  rightAgents: string[];
  leftTools: string[];
  rightTools: string[];
}

const agentIds = (r: ChatResponse) => (r.context.agents ?? []).map((a) => a.agent_id).sort();
const tools = (r: ChatResponse) => [...(r.context.tools_used ?? [])].sort();
const same = (a: string[], b: string[]) => a.join(",") === b.join(",");

/** Compare the "before" (manual) and "after" (automatic) answers to the same question. */
export function compare(left: ChatResponse, right: ChatResponse): Comparison {
  const leftAgents = agentIds(left);
  const rightAgents = agentIds(right);
  const leftTools = tools(left);
  const rightTools = tools(right);
  const sameAgents = same(leftAgents, rightAgents);
  const sameTools = same(leftTools, rightTools);
  const sameAnswer = left.response.trim() === right.response.trim();

  // Only the agent choice is the user's. Which tools an agent calls, and the wording, can vary between two
  // separate model runs, so with the same agents the sides match even if those differ slightly.
  let headline: string;
  if (right.status === "clarify") {
    headline = "The two sides differ: the platform asked what you meant instead of guessing.";
  } else if (!sameAgents) {
    headline = "The two sides differ: your selection didn't match what the question needed.";
  } else if (sameTools && sameAnswer) {
    headline = "Both sides match: same agents, same tools, same answer.";
  } else if (sameTools) {
    headline = "Same agents and tools. The wording differs slightly because each side is a separate model response.";
  } else {
    headline =
      "Same agents. They called slightly different tools, because each side is a separate model run; your selection matched.";
  }
  return {
    headline,
    match: sameAgents && right.status !== "clarify",
    sameAgents,
    sameTools,
    sameAnswer,
    leftAgents,
    rightAgents,
    leftTools,
    rightTools,
  };
}

export const listOrNone = (xs: string[]) => (xs.length ? xs.join(", ") : "none");
export const usd = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;
