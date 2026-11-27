// Types and calls for the FastAPI backend. In dev, Vite proxies /v1 and /health to it.

export interface AgentCard {
  id: string;
  name: string;
  description: string;
  keywords: string[];
  tools: string[];
  sources?: string[];
  files?: string[];
}

export interface Health {
  status: string;
  llm: string; // "mock" | "anthropic"
  agents: string[];
  models?: { intent: string; agent: string; collate: string };
  budget_remaining_usd?: number;
}

export interface AgentRun {
  agent_id: string;
  name?: string;
  status?: string;
  attempts?: number;
}

export interface FirstRun {
  llm_calls: number;
  latency_ms: number;
  cost_usd: number;
  trace_id?: string;
}

/** What the plan cache did for this answer. */
export interface CacheInfo {
  hit: boolean;
  reason?: string; // why it missed
  plan_id?: string;
  similarity?: number;
  plan_question?: string;
  hits?: number;
  first_run?: FirstRun; // the full run that built the plan, for comparison
  saved?: boolean; // a full run stored its plan
  save_note?: string;
}

export interface ChatContext {
  agents?: AgentRun[];
  tools_used?: string[];
  llm_calls?: number;
  llm_calls_by_step?: Record<string, number>;
  cost_usd?: number;
  latency_ms?: number;
  budget_remaining_usd?: number;
  orchestrator_loops?: number;
  cache?: CacheInfo;
  detail?: boolean;
  answered_question?: string;
}

export type ChatStatus = "ok" | "partial" | "clarify" | "out_of_scope" | "error" | "budget_exceeded";

export interface ChatResponse {
  session_id: string;
  trace_id: string;
  status: ChatStatus | string;
  response: string;
  path: string;
  context: ChatContext;
}

export interface ChatRequest {
  customer_id: string;
  message: string;
  session_id: string | null;
  more_details?: boolean;
}

export interface CacheListing {
  enabled: boolean;
  count: number;
  plans: { id: string; agents: string[]; slots: string[]; question: string; hits: number }[];
}

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function deleteJson<T>(url: string): Promise<T> {
  const r = await fetch(url, { method: "DELETE" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  let data: unknown = null;
  try {
    data = await r.json();
  } catch {
    // non-JSON error body
  }
  if (!r.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail;
    throw new Error(detail ? (typeof detail === "string" ? detail : JSON.stringify(detail)) : `HTTP ${r.status}`);
  }
  return data as T;
}

export const api = {
  health: () => getJson<Health>("/health"),
  agents: () => getJson<AgentCard[]>("/v1/agents"),
  /** After: the platform picks the agents. */
  chat: (req: ChatRequest) => postJson<ChatResponse>("/v1/chat", req),
  /** Before: the user picks the agents. */
  chatManual: (req: ChatRequest & { agent_ids: string[] }) => postJson<ChatResponse>("/v1/chat/manual", req),
  auditUrl: (traceId: string) => `/v1/audit/${encodeURIComponent(traceId)}`,
  /** Stored question-to-tool plans. */
  cache: () => getJson<CacheListing>("/v1/cache"),
  clearCache: () => deleteJson<{ cleared: number }>("/v1/cache"),
};
