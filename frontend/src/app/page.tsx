"use client";

import AuthGate from "@/components/AuthGate";
import ChatScreen from "@/components/ChatScreen";

export default function HomePage() {
  return <AuthGate>{(user, logout) => <ChatScreen user={user} onLogout={logout} />}</AuthGate>;
}
