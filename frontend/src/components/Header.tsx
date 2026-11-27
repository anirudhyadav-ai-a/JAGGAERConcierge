import type { Health } from "../api";

export const CUSTOMERS = [
  { id: "C1001", label: "C1001 (demo customer)" },
  { id: "C2002", label: "C2002 (second customer)" },
];

interface Props {
  health: Health | null;
  loadError: boolean;
  budget: number | undefined;
  plans?: number;
  onClearPlans?: () => void;
  customer: string;
  onCustomerChange: (id: string) => void;
}

export function Header({ health, loadError, budget, plans, onClearPlans, customer, onCustomerChange }: Props) {
  const provider = loadError
    ? "server not reachable"
    : !health
      ? "connecting…"
      : health.llm === "anthropic"
        ? "Claude"
        : "Test mock (not Claude)";
  return (
    <div className="top">
      <div>
        <h1>Ask once, compare</h1>
        <p>The same question, answered the old way (you pick the agents) and the new way (the platform picks them).</p>
      </div>
      <div className="env">
        <span>
          Model: <strong data-testid="provider">{provider}</strong>
        </span>
        {budget !== undefined && (
          <span>
            Budget left: <strong data-testid="budget">${budget.toFixed(2)}</strong>
          </span>
        )}
        {plans !== undefined && (
          <span>
            Cached plans: <strong data-testid="plans">{plans}</strong>
            {plans > 0 && onClearPlans && (
              <button type="button" className="ghost inline" onClick={onClearPlans}>
                Clear
              </button>
            )}
          </span>
        )}
        <label>
          Customer{" "}
          <select value={customer} onChange={(e) => onCustomerChange(e.target.value)}>
            {CUSTOMERS.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
      </div>
    </div>
  );
}
