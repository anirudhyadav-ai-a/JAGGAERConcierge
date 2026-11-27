import type { AgentCard } from "../api";

interface Props {
  agents: AgentCard[];
  selected: string[];
  onToggle: (id: string) => void;
  warn: boolean;
}

export function AgentPicker({ agents, selected, onToggle, warn }: Props) {
  const n = selected.length;
  const hint = warn
    ? "Nothing was asked here: tick at least one agent first. This is the step the new way removes."
    : n
      ? `${n} agent${n > 1 ? "s" : ""} selected. Only ${n > 1 ? "these" : "this one"} will work on the question.`
      : "Tick at least one agent.";
  return (
    <>
      <ul className="agents" aria-label="Agents to ask">
        {agents.map((a) => (
          <li key={a.id}>
            <label className={`agent${selected.includes(a.id) ? " picked" : ""}`}>
              <input type="checkbox" value={a.id} checked={selected.includes(a.id)} onChange={() => onToggle(a.id)} />
              <span>
                <span className="id">{a.id}</span> <span className="name">{a.name}</span>
                <span className="tools">{a.tools.join(", ")}</span>
              </span>
            </label>
          </li>
        ))}
      </ul>
      <div className={`hint${warn ? " warn" : ""}`} data-testid="hint">
        {hint}
      </div>
    </>
  );
}
