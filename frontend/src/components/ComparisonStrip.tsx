import type { ChatResponse } from "../api";
import { compare, listOrNone, usd } from "../compare";

function Mark({ ok }: { ok: boolean }) {
  return ok ? <span className="ok">Same</span> : <span className="no">Different</span>;
}

export function ComparisonStrip({ left, right }: { left: ChatResponse; right: ChatResponse }) {
  const c = compare(left, right);
  return (
    <section className="ask" aria-live="polite">
      <div className={`check ${c.match ? "same" : "diff"}`} data-testid="comparison">
        <h2>{c.headline}</h2>
        <ul>
          <li>
            <b>Agents</b>
            <Mark ok={c.sameAgents} />: you picked {listOrNone(c.leftAgents)}; the platform picked{" "}
            {listOrNone(c.rightAgents)}
          </li>
          <li>
            <b>Tools</b>
            <Mark ok={c.sameTools} />:{" "}
            {c.sameTools
              ? listOrNone(c.leftTools)
              : `you ran ${listOrNone(c.leftTools)}; it ran ${listOrNone(c.rightTools)}`}
          </li>
          <li>
            <b>Cost and calls</b>
            you {usd(left.context.cost_usd)}, {left.context.llm_calls ?? 0} calls; platform{" "}
            {usd(right.context.cost_usd)}, {right.context.llm_calls ?? 0} calls
          </li>
        </ul>
      </div>
    </section>
  );
}
