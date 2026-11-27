import { useRef, type FormEvent, type KeyboardEvent } from "react";

export const EXAMPLES = [
  { label: "Ambiguous", q: "What about my card?" },
  { label: "One agent, two tools", q: "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?" },
  { label: "Two agents, one tool each", q: "What is the late payment fee, and how much did I spend on dining this month?" },
  { label: "One agent needs another", q: "How much did I spend this month and which credit card suits my spending?" },
];

interface Props {
  question: string;
  onQuestionChange: (q: string) => void;
  onSubmit: () => void;
  onReset: () => void;
  busy: boolean;
}

export function AskBar({ question, onQuestionChange, onSubmit, onReset, busy }: Props) {
  const box = useRef<HTMLTextAreaElement>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (question.trim() && !busy) onSubmit();
  };
  // Enter sends, Shift+Enter adds a new line.
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (question.trim() && !busy) onSubmit();
    }
  };

  return (
    <section className="ask" aria-label="Question">
      <div className="askbox">
        <form className="askrow" onSubmit={submit}>
          <label htmlFor="question" className="visually-hidden">
            Your question
          </label>
          <textarea
            id="question"
            ref={box}
            rows={2}
            placeholder="Ask about your cards, transactions, bank policies or spending…"
            value={question}
            onChange={(e) => onQuestionChange(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button className="primary" type="submit" disabled={busy}>
            {busy ? "Asking…" : "Ask both"}
          </button>
        </form>
        <div className="examples">
          <span>Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.label}
              className="chip"
              type="button"
              onClick={() => {
                onQuestionChange(ex.q);
                box.current?.focus();
              }}
            >
              {ex.label}
            </button>
          ))}
          <button className="ghost" type="button" onClick={onReset}>
            Start over
          </button>
        </div>
      </div>
    </section>
  );
}
