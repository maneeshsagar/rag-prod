"use client";

// Question box. Enter sends, Shift+Enter adds a new line.

import { FormEvent, KeyboardEvent, useState } from "react";

type Props = { disabled: boolean; onSend: (question: string) => void };

export default function ChatInput({ disabled, onSend }: Props) {
  const [text, setText] = useState("");

  function send(event?: FormEvent) {
    event?.preventDefault();
    const question = text.trim();
    if (!question || disabled) return;
    onSend(question);
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <form className="chat-input" onSubmit={send}>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question about the documents…"
        rows={2}
        maxLength={4000}
      />
      <button className="primary" disabled={disabled || !text.trim()}>Send</button>
    </form>
  );
}
