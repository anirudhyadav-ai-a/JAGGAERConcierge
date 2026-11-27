import { useEffect, useRef, useState } from "react";
import { api, type AgentCard, type ChatResponse, type Health } from "./api";
import { AgentPicker } from "./components/AgentPicker";
import { AskBar } from "./components/AskBar";
import { ComparisonStrip } from "./components/ComparisonStrip";
import { CUSTOMERS, Header } from "./components/Header";
import { Thread, type TurnState } from "./components/Turn";

type Side = "before" | "after";

export const LOAD_RETRIES = 10;
const RETRY_MS = 1000;

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [agents, setAgents] = useState<AgentCard[]>([]);
  const [budget, setBudget] = useState<number | undefined>(undefined);

  const [customer, setCustomer] = useState(CUSTOMERS[0].id);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [pickerWarn, setPickerWarn] = useState(false);

  const [turns, setTurns] = useState<Record<Side, TurnState[]>>({ before: [], after: [] });
  const [pair, setPair] = useState<{ left: ChatResponse; right: ChatResponse } | null>(null);

  // Each side keeps its own conversation. Refs, so a reset mid-request can't be undone by a late reply.
  const sessions = useRef<Record<Side, string | null>>({ before: null, after: null });
  const generation = useRef(0);
  const nextId = useRef(1);

  useEffect(() => {
    // The backend can start a few seconds after the dev server (`make dev`), so retry before giving up.
    let cancelled = false;
    const load = async (attemptsLeft: number) => {
      try {
        const [h, catalog] = await Promise.all([api.health(), api.agents()]);
        if (cancelled) return;
        setHealth(h);
        setAgents(catalog);
        setBudget(h.budget_remaining_usd);
        setLoadError(false);
      } catch {
        if (cancelled) return;
        if (attemptsLeft > 0) setTimeout(() => load(attemptsLeft - 1), RETRY_MS);
        else setLoadError(true);
      }
    };
    load(LOAD_RETRIES);
    return () => {
      cancelled = true;
    };
  }, []);

  const reset = () => {
    generation.current += 1;
    sessions.current = { before: null, after: null };
    setTurns({ before: [], after: [] });
    setPair(null);
    setPickerWarn(false);
    setBusy(false);
  };

  const toggle = (id: string) => {
    setPickerWarn(false);
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  };

  const addTurn = (side: Side, q: string): number => {
    const id = nextId.current++;
    setTurns((t) => ({ ...t, [side]: [{ id, question: q }, ...t[side]] }));
    return id;
  };

  const settle = (side: Side, id: number, patch: Partial<TurnState>) =>
    setTurns((t) => ({ ...t, [side]: t[side].map((x) => (x.id === id ? { ...x, ...patch } : x)) }));

  async function ask(side: Side, q: string, gen: number): Promise<ChatResponse | null> {
    // Send in catalog order so the request doesn't depend on the order boxes were ticked.
    const ids = agents.map((a) => a.id).filter((id) => selected.includes(id));
    if (side === "before" && ids.length === 0) {
      setPickerWarn(true);
      return null;
    }
    const id = addTurn(side, q);
    try {
      const req = { customer_id: customer, message: q, session_id: sessions.current[side] };
      const r = side === "before" ? await api.chatManual({ ...req, agent_ids: ids }) : await api.chat(req);
      if (gen !== generation.current) return null;
      sessions.current[side] = r.session_id;
      settle(side, id, { response: r });
      if (r.context.budget_remaining_usd !== undefined) setBudget(r.context.budget_remaining_usd);
      return r;
    } catch (e) {
      if (gen === generation.current) settle(side, id, { error: (e as Error).message });
      return null;
    }
  }

  const askBoth = async () => {
    const q = question.trim();
    if (!q) return;
    const gen = generation.current;
    setBusy(true);
    setPair(null);
    const [left, right] = await Promise.all([ask("before", q, gen), ask("after", q, gen)]);
    if (gen !== generation.current) return;
    setPair(left && right ? { left, right } : null);
    setBusy(false);
  };

  const changeCustomer = (id: string) => {
    setCustomer(id);
    reset();
  };

  return (
    <>
      <Header
        health={health}
        loadError={loadError}
        budget={budget}
        customer={customer}
        onCustomerChange={changeCustomer}
      />
      <AskBar question={question} onQuestionChange={setQuestion} onSubmit={askBoth} onReset={reset} busy={busy} />
      {pair && <ComparisonStrip left={pair.left} right={pair.right} />}

      <main className="split">
        <section className="side before" aria-labelledby="beforeTitle">
          <header>
            <h2 id="beforeTitle">
              Before <span className="tag">You choose the agents</span>
            </h2>
            <p>Tick the agents you think are needed. Only those agents work on the question.</p>
          </header>
          <AgentPicker agents={agents} selected={selected} onToggle={toggle} warn={pickerWarn} />
          <Thread
            turns={turns.before}
            emptyText="The answer from the agents you pick appears here."
            agentLabel="Agents you picked"
          />
        </section>

        <section className="side after" aria-labelledby="afterTitle">
          <header>
            <h2 id="afterTitle">
              After <span className="tag">The platform chooses</span>
            </h2>
            <p>Nothing to tick. The platform picks the agents from the question.</p>
          </header>
          <Thread turns={turns.after} emptyText="The combined answer appears here." agentLabel="Agents it picked" />
        </section>
      </main>
    </>
  );
}
