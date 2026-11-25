"use client";

// Right column: the sources cited by the selected answer, with the supporting quote.

import type { Citation, ChatMessage } from "@/lib/types";

type Props = { message: ChatMessage | null; highlighted: number | null };

export default function SourcesPanel({ message, highlighted }: Props) {
  const citations: Citation[] = message?.citations ?? [];
  const pending = message?.streaming ? message.sources ?? [] : [];

  return (
    <aside className="sources">
      <h2>Sources</h2>
      {!message && <p className="muted small">Ask a question to see its sources here.</p>}

      {pending.length > 0 && (
        <p className="muted small">Reading {pending.length} passages…</p>
      )}

      {citations.map((c) => (
        <div key={c.number} id={`source-${c.number}`}
             className={`source ${c.number === highlighted ? "highlight" : ""}`}>
          <div className="source-head">
            <span className="cite static">{c.number}</span>
            <strong>{c.title}</strong>
          </div>
          <div className="muted small">
            {c.citation_label}
            {c.section_path && ` · ${c.section_path}`}
          </div>
          {c.quote && <blockquote>{c.quote}</blockquote>}
          {!c.supported && <p className="warning small">Check this citation: weak match with the source.</p>}
        </div>
      ))}

      {message && !message.streaming && citations.length === 0 && (
        <p className="muted small">This answer has no citations.</p>
      )}
    </aside>
  );
}
