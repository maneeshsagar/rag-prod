"use client";

// The chat screen: sidebar | messages + input | sources.
//
// Every conversation keeps its own message list (`chats`, keyed by conversation id),
// so an answer keeps streaming into its own chat while you look at another one.
// A new chat uses a temporary "draft-…" key until the server returns its id.

import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";
import { streamChat } from "@/lib/chatStream";
import type { ChatMessage, Citation, ConversationSummary, SourceInfo, User } from "@/lib/types";
import ChatInput from "./ChatInput";
import MessageView from "./MessageView";
import Sidebar from "./Sidebar";
import SourcesPanel from "./SourcesPanel";

type Props = { user: User; onLogout: () => void };
type ServerMessage = ChatMessage & { id: string };

const POLL_MS = 2000;
const NO_MESSAGES: ChatMessage[] = []; // shared, so an empty chat doesn't re-trigger effects
let draftCounter = 0;
const newDraftKey = () => `draft-${++draftCounter}`;
const isDraft = (key: string) => key.startsWith("draft-");

export default function ChatScreen({ user, onLogout }: Props) {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [chats, setChats] = useState<Record<string, ChatMessage[]>>({});
  const [activeKey, setActiveKey] = useState<string>(() => newDraftKey());
  // Which answer's sources are shown, and which citation is highlighted (per chat).
  const [selection, setSelection] = useState<{ key: string; index: number; cite: number | null } | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const messages = chats[activeKey] ?? NO_MESSAGES;
  const busy = messages.some((m) => m.streaming);
  const selectedIndex = selection?.key === activeKey ? selection.index : lastAnswerIndex(messages);
  const highlighted = selection?.key === activeKey ? selection.cite : null;

  const loadConversations = useCallback(() => {
    apiFetch<ConversationSummary[]>("/conversations").then(setConversations).catch(() => undefined);
  }, []);

  // Fetch a conversation from the server. If its last message is a question, the server
  // is still writing the answer (e.g. after a page reload): show a placeholder.
  const fetchConversation = useCallback(async (id: string) => {
    const data = await apiFetch<{ messages: ServerMessage[] }>(`/conversations/${id}`);
    const loaded: ChatMessage[] = data.messages.map((m) => ({ ...m, citations: m.citations ?? [] }));
    if (loaded.at(-1)?.role === "user") {
      loaded.push({ role: "assistant", content: "", citations: [], serverPending: true,
                    streaming: true, status: "Still writing the answer" });
    }
    setChats((all) => (all[id]?.some((m) => m.streaming && !m.serverPending) ? all : { ...all, [id]: loaded }));
  }, []);

  // Effect bodies use braces so they return nothing (React treats a returned value as cleanup).
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // While the server is still writing an answer we are not streaming, check back regularly.
  const waitingOnServer = messages.at(-1)?.serverPending === true;
  useEffect(() => {
    if (!waitingOnServer) return;
    const timer = setInterval(() => {
      fetchConversation(activeKey).catch(() => undefined);
      loadConversations();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [waitingOnServer, activeKey, fetchConversation, loadConversations]);

  function openConversation(id: string) {
    setActiveKey(id);
    const local = chats[id];
    const streamingHere = local?.some((m) => m.streaming && !m.serverPending);
    if (!streamingHere) fetchConversation(id).catch(() => undefined);
  }

  function newChat() {
    setActiveKey(newDraftKey());
  }

  async function deleteConversation(id: string) {
    await apiFetch(`/conversations/${id}`, { method: "DELETE" }).catch(() => undefined);
    setChats((all) => {
      const copy = { ...all };
      delete copy[id];
      return copy;
    });
    if (id === activeKey) newChat();
    loadConversations();
  }

  async function ask(question: string) {
    let key = activeKey; // changes from "draft-…" to the real id once the server creates it
    const updateLast = (change: Partial<ChatMessage>) =>
      setChats((all) => {
        const list = [...(all[key] ?? [])];
        list[list.length - 1] = { ...list[list.length - 1], ...change };
        return { ...all, [key]: list };
      });

    setChats((all) => ({
      ...all,
      [key]: [
        ...(all[key] ?? []),
        { role: "user", content: question, citations: [] },
        { role: "assistant", content: "", citations: [], streaming: true, status: "Starting" },
      ],
    }));
    setSelection(null);

    let text = "";
    try {
      await streamChat(question, isDraft(key) ? null : key, (event, data) => {
        if (event === "conversation") {
          const realId = data.conversation_id as string;
          if (realId !== key) {
            const oldKey = key;
            setChats((all) => {
              const copy = { ...all, [realId]: all[oldKey] ?? [] };
              delete copy[oldKey];
              return copy;
            });
            setActiveKey((current) => (current === oldKey ? realId : current));
            key = realId;
          }
          loadConversations();
        }
        if (event === "status") updateLast({ status: data.message as string });
        if (event === "sources") updateLast({ sources: data.sources as SourceInfo[] });
        if (event === "token") {
          text += data.text as string;
          updateLast({ content: text, status: undefined });
        }
        if (event === "done") {
          updateLast({
            id: data.message_id as string,
            content: data.answer as string,
            citations: data.citations as Citation[],
            route: data.route as string,
            notFound: data.not_found as boolean,
            streaming: false,
          });
        }
        if (event === "error") updateLast({ error: data.message as string, streaming: false });
      });
    } catch (err) {
      updateLast({ error: err instanceof Error ? err.message : "Request failed", streaming: false });
    } finally {
      updateLast({ streaming: false });
      loadConversations();
    }
  }

  function showCitation(index: number, cite: number) {
    setSelection({ key: activeKey, index, cite });
    setTimeout(() => document.getElementById(`source-${cite}`)?.scrollIntoView({ behavior: "smooth" }), 0);
  }

  return (
    <div className="layout">
      <Sidebar
        user={user}
        conversations={conversations}
        activeId={isDraft(activeKey) ? null : activeKey}
        busyIds={Object.keys(chats).filter((k) => chats[k].some((m) => m.streaming))}
        onSelect={openConversation}
        onNewChat={newChat}
        onDelete={deleteConversation}
        onLogout={onLogout}
      />

      <main className="chat">
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <h2>Ask about your documents</h2>
              <p className="muted">Answers cite the page, slide or section they come from.</p>
            </div>
          )}
          {messages.map((message, index) => (
            <MessageView
              key={index}
              message={message}
              selected={index === selectedIndex}
              onCitationClick={(n) => showCitation(index, n)}
            />
          ))}
          <div ref={bottomRef} />
        </div>
        <ChatInput disabled={busy} onSend={ask} />
      </main>

      <SourcesPanel message={selectedIndex !== null ? messages[selectedIndex] ?? null : null} highlighted={highlighted} />
    </div>
  );
}

function lastAnswerIndex(messages: ChatMessage[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === "assistant") return i;
  return null;
}
