// POST /chat and read the Server-Sent Events stream it returns.
//
// The backend sends events like:
//   event: token
//   data: {"text": "Employees receive"}
//
// onEvent is called once per event with its name and parsed data.

import { API_URL, getToken } from "./api";

export type ChatEventHandler = (event: string, data: Record<string, unknown>) => void;

export async function streamChat(
  question: string,
  conversationId: string | null,
  onEvent: ChatEventHandler,
): Promise<void> {
  const response = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken() ?? ""}` },
    body: JSON.stringify({ question, conversation_id: conversationId }),
  });
  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Chat request failed (${response.status})`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Events are separated by a blank line.
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      handleRawEvent(raw, onEvent);
      boundary = buffer.indexOf("\n\n");
    }
  }
}

function handleRawEvent(raw: string, onEvent: ChatEventHandler): void {
  let name = "message";
  let data = "";
  for (const line of raw.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7);
    else if (line.startsWith("data: ")) data += line.slice(6);
  }
  if (data) onEvent(name, JSON.parse(data));
}
