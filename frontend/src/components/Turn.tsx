import { api, type ChatResponse } from "../api";
import { statusLabel } from "../compare";
import { Answer } from "./Answer";

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

/** How the answer was produced: a reused plan, the full flow, or a detailed re-run. */
function PathPill({ response }: { response: ChatResponse }) {
  if (response.path === "cache") return <span className="path cache">From cache</span>;
  if (response.context.detail) return <span className="path detail">Detailed answer</span>;
  if (response.path === "full" || response.path === "manual") return <span className="path full">Full flow</span>;
  return null;
}

const secs = (ms: number | undefined) => `${((ms ?? 0) / 1000).toFixed(1)} s`;
const usd = (n: number | undefined) => `$${(n ?? 0).toFixed(4)}`;

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
      <dd>{usd(ctx.cost_usd)}</dd>
      <dt>Time</dt>
      <dd>{secs(ctx.latency_ms)}</dd>
    </dl>
  );
}

/** For a cache hit: this answer next to the full run that built the plan. Otherwise: what the cache did. */
function CacheNote({ response }: { response: ChatResponse }) {
  const c = response.context.cache;
  if (!c) return null;
  if (c.hit && c.first_run) {
    const f = c.first_run;
    return (
      <div className="cache-note hit" data-testid="cache-note">
        <strong>Reused a saved plan</strong> (similarity {c.similarity?.toFixed(2)}): the tools ran live, with no agent
        reasoning steps.
        <ul>
          <li>
            LLM calls: <b>{response.context.llm_calls ?? 0}</b> instead of {f.llm_calls}
          </li>
          <li>
            Time: <b>{secs(response.context.latency_ms)}</b> instead of {secs(f.latency_ms)}
          </li>
          <li>
            Cost: <b>{usd(response.context.cost_usd)}</b> instead of {usd(f.cost_usd)}
          </li>
        </ul>
      </div>
    );
  }
  if (c.saved) {
    return (
      <p className="cache-note" data-testid="cache-note">
        Plan saved. Ask this again, or a similar question, to see it answered from cache.
      </p>
    );
  }
  const why = c.reason && c.reason !== "no plan for this set of agents" ? c.reason : c.save_note;
  return why ? (
    <p className="cache-note" data-testid="cache-note">
      Not from cache: {why}.
    </p>
  ) : null;
}

interface TurnProps {
  turn: TurnState;
  agentLabel: string;
  onMoreDetails?: () => void;
}

export function Turn({ turn, agentLabel, onMoreDetails }: TurnProps) {
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
          <div className="pills">
            <StatusPill status={response.status} />
            <PathPill response={response} />
          </div>
          {response.context.detail && response.context.answered_question && (
            <p className="q">More detail on: {response.context.answered_question}</p>
          )}
          <Answer text={response.response} />
          <Facts response={response} agentLabel={agentLabel} />
          <CacheNote response={response} />
          <p className="trace">
            Audit trail:{" "}
            <a href={api.auditUrl(response.trace_id)} target="_blank" rel="noopener noreferrer">
              {response.trace_id}
            </a>
            {onMoreDetails && response.status === "ok" && (
              <button type="button" className="ghost inline" onClick={onMoreDetails}>
                More details
              </button>
            )}
          </p>
        </>
      )}
    </article>
  );
}

interface ThreadProps {
  turns: TurnState[];
  emptyText: string;
  agentLabel: string;
  onMoreDetails?: () => void;
}

/** Oldest first, so a question asked again shows below its first answer. */
export function Thread({ turns, emptyText, agentLabel, onMoreDetails }: ThreadProps) {
  const last = turns.length - 1;
  return (
    <div className="thread" aria-live="polite">
      {turns.length === 0 ? (
        <p className="empty">{emptyText}</p>
      ) : (
        turns.map((t, i) => (
          <Turn key={t.id} turn={t} agentLabel={agentLabel} onMoreDetails={i === last ? onMoreDetails : undefined} />
        ))
      )}
    </div>
  );
}
