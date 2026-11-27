import { api, type ChatResponse } from "../api";
import { statusLabel } from "../compare";

export interface TurnState {
  id: number;
  question: string;
  response?: ChatResponse;
  error?: string;
}

function StatusPill({ status }: { status: string }) {
  const { label, tone } = statusLabel(status);
  return <span className={`status ${tone}`}>{label}</span>;
}

function Facts({ response, agentLabel }: { response: ChatResponse; agentLabel: string }) {
  const ctx = response.context;
  const agents = (ctx.agents ?? []).map((a) => a.agent_id).join(", ") || "none";
  const tools = (ctx.tools_used ?? []).join(", ") || "none";
  return (
    <dl className="facts">
      <dt>{agentLabel}</dt>
      <dd>{agents}</dd>
      <dt>Tools run</dt>
      <dd>{tools}</dd>
      <dt>LLM calls</dt>
      <dd>{ctx.llm_calls ?? 0}</dd>
      <dt>Cost</dt>
      <dd>${(ctx.cost_usd ?? 0).toFixed(4)}</dd>
      <dt>Time</dt>
      <dd>{((ctx.latency_ms ?? 0) / 1000).toFixed(1)} s</dd>
    </dl>
  );
}

export function Turn({ turn, agentLabel }: { turn: TurnState; agentLabel: string }) {
  const { question, response, error } = turn;
  return (
    <article className="turn">
      <p className="q">{question}</p>
      {!response && !error && <p className="working">Working…</p>}
      {error && (
        <>
          <StatusPill status="error" />
          <p className="a">Request failed: {error}</p>
        </>
      )}
      {response && (
        <>
          <StatusPill status={response.status} />
          <p className="a">{response.response}</p>
          <Facts response={response} agentLabel={agentLabel} />
          <p className="trace">
            Audit trail:{" "}
            <a href={api.auditUrl(response.trace_id)} target="_blank" rel="noopener noreferrer">
              {response.trace_id}
            </a>
          </p>
        </>
      )}
    </article>
  );
}

export function Thread({ turns, emptyText, agentLabel }: { turns: TurnState[]; emptyText: string; agentLabel: string }) {
  return (
    <div className="thread" aria-live="polite">
      {turns.length === 0 ? (
        <p className="empty">{emptyText}</p>
      ) : (
        turns.map((t) => <Turn key={t.id} turn={t} agentLabel={agentLabel} />)
      )}
    </div>
  );
}
