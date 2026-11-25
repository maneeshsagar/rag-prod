"use client";

// One chat message. Assistant answers are rendered by AnswerText (lists, bold,
// clickable citation chips) and get 👍 / 👎 feedback buttons.

import { useState } from "react";
import AnswerText from "./AnswerText";
import { apiFetch } from "@/lib/api";
import type { ChatMessage } from "@/lib/types";

type Props = {
  message: ChatMessage;
  selected: boolean;
  onCitationClick: (number: number) => void;
};

export default function MessageView({ message, selected, onCitationClick }: Props) {
  if (message.role === "user") {
    return <div className="message user"><div className="bubble">{message.content}</div></div>;
  }

  return (
    <div className={`message assistant ${selected ? "selected" : ""}`}>
      {message.status && message.streaming && <p className="status">{message.status}…</p>}
      {message.content && (
        <div className={`answer ${message.notFound ? "not-found" : ""}`}>
          <AnswerText text={message.content} onCitationClick={onCitationClick} />
        </div>
      )}
      {message.error && <p className="error">{message.error}</p>}
      {!message.streaming && message.id && <Feedback messageId={message.id} text={message.content} />}
    </div>
  );
}

function Feedback({ messageId, text }: { messageId: string; text: string }) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [comment, setComment] = useState("");
  const [saved, setSaved] = useState(false);

  async function send(value: 1 | -1, note?: string) {
    setRating(value);
    await apiFetch("/feedback", {
      method: "POST",
      body: JSON.stringify({ message_id: messageId, rating: value, comment: note || null }),
    }).catch(() => undefined);
    if (note !== undefined) setSaved(true);
  }

  return (
    <div className="feedback">
      <button className={`icon ${rating === 1 ? "on" : ""}`} onClick={() => send(1)} aria-label="Good answer">👍</button>
      <button className={`icon ${rating === -1 ? "on" : ""}`} onClick={() => send(-1)} aria-label="Bad answer">👎</button>
      <button className="icon" onClick={() => navigator.clipboard?.writeText(text)} aria-label="Copy">📋</button>
      {rating === -1 && !saved && (
        <form onSubmit={(e) => { e.preventDefault(); send(-1, comment); }} className="feedback-comment">
          <input placeholder="What was wrong?" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="small">Send</button>
        </form>
      )}
      {saved && <span className="muted small">Thanks for the feedback</span>}
    </div>
  );
}
