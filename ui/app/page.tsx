"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

type Status = "ok" | "not_available" | "not_in_scope" | "not_allowed" | "error";

type Evidence = {
  row_count: number;
  truncated: boolean;
  rows: Record<string, unknown>[];
  sql?: string;
};

type Message =
  | { role: "user"; text: string }
  | { role: "assistant"; text: string; status: Status; evidence?: Evidence | null };

const SUGGESTIONS = [
  "Who are the top 5 artists by sales?",
  "Which genre has the most tracks?",
  "How much revenue did we make in 2023?",
  "Which employee supports the most customers?",
];

const STATUS_LABEL: Partial<Record<Status, string>> = {
  not_available: "Not available",
  not_in_scope: "Not in scope",
  not_allowed: "Read-only",
  error: "Error",
};

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  async function send(text: string) {
    const query = text.trim();
    if (!query || pending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: query }]);
    setPending(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, session_id: sessionId }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = await res.json();
      setSessionId(data.session_id);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: data.results, status: data.status, evidence: data.evidence },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: "assistant", text: "Sorry, I couldn't reach the server. Please try again.", status: "error" },
      ]);
    } finally {
      setPending(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    send(input);
  }

  function newChat() {
    setMessages([]);
    setSessionId(null);
  }

  return (
    <main className="shell">
      <header className="top">
        <div>
          <h1>Synthio</h1>
          <p className="sub">Ask about the Chinook music store: artists, tracks, customers, sales.</p>
        </div>
        {messages.length > 0 && (
          <button className="ghost" onClick={newChat} disabled={pending}>
            New chat
          </button>
        )}
      </header>

      <section className="thread" aria-live="polite">
        {messages.length === 0 && (
          <div className="empty">
            <p>Try one of these:</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chip" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="msg user">
              {m.text}
            </div>
          ) : (
            <div key={i} className={`msg assistant ${m.status}`}>
              {STATUS_LABEL[m.status] && <span className="badge">{STATUS_LABEL[m.status]}</span>}
              <div className="text">{m.text}</div>
              {m.evidence && <EvidenceView evidence={m.evidence} />}
            </div>
          ),
        )}

        {pending && (
          <div className="msg assistant thinking">
            <span className="dot" />
            <span className="dot" />
            <span className="dot" />
          </div>
        )}
        <div ref={endRef} />
      </section>

      <form className="composer" onSubmit={onSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question…"
          maxLength={1000}
          disabled={pending}
          autoFocus
        />
        <button type="submit" disabled={pending || !input.trim()}>
          Send
        </button>
      </form>
    </main>
  );
}

function EvidenceView({ evidence }: { evidence: Evidence }) {
  const columns = evidence.rows.length ? Object.keys(evidence.rows[0]) : [];
  const shown = evidence.rows.length;
  return (
    <details className="evidence">
      <summary>
        Evidence · {evidence.row_count} {evidence.row_count === 1 ? "record" : "records"}
        {evidence.truncated ? " (more not shown)" : ""}
      </summary>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {evidence.rows.map((r, i) => (
              <tr key={i}>
                {columns.map((c) => (
                  <td key={c}>{formatCell(r[c])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown < evidence.row_count && <p className="note">Showing first {shown}.</p>}
      {evidence.sql && <pre className="sql">{evidence.sql}</pre>}
    </details>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(v);
}
