"use client";

// Left column: new chat, conversation history, link to documents, logout.

import Link from "next/link";
import type { ConversationSummary, User } from "@/lib/types";

type Props = {
  user: User;
  conversations: ConversationSummary[];
  activeId: string | null;
  busyIds: string[]; // conversations with an answer still being written
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete: (id: string) => void;
  onLogout: () => void;
};

export default function Sidebar(props: Props) {
  const { user, conversations, activeId, busyIds, onSelect, onNewChat, onDelete, onLogout } = props;

  return (
    <aside className="sidebar">
      <button className="primary wide" onClick={onNewChat}>+ New chat</button>

      <nav className="conversation-list">
        {conversations.length === 0 && <p className="muted small">No conversations yet.</p>}
        {conversations.map((c) => (
          <div key={c.id} className={`conversation ${c.id === activeId ? "active" : ""}`}>
            <button className="link" onClick={() => onSelect(c.id)} title={c.title}>
              {busyIds.includes(c.id) && <span className="busy-dot" aria-label="Answering" />}
              {c.title}
            </button>
            <button className="icon" onClick={() => onDelete(c.id)} aria-label="Delete conversation">
              ×
            </button>
          </div>
        ))}
      </nav>

      <div className="sidebar-footer">
        <Link href="/documents">Documents</Link>
        <span className="muted small">{user.name}</span>
        <button className="link small" onClick={onLogout}>Log out</button>
      </div>
    </aside>
  );
}
