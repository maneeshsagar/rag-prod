"use client";

// Shows the login form until the user is logged in, then renders the page.

import { FormEvent, ReactNode, useEffect, useState } from "react";
import { apiFetch, getToken, setToken } from "@/lib/api";
import type { User } from "@/lib/types";

type Props = { children: (user: User, logout: () => void) => ReactNode };

export default function AuthGate({ children }: Props) {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);

  // On first load: if a token is saved, ask the API who it belongs to.
  useEffect(() => {
    let cancelled = false;
    const check = getToken()
      ? apiFetch<User>("/auth/me").catch(() => null)
      : Promise.resolve(null);
    check.then((found) => {
      if (cancelled) return;
      if (!found) setToken(null);
      setUser(found);
      setChecking(false);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const logout = () => {
    setToken(null);
    setUser(null);
  };

  if (checking) return <div className="center muted">Loading…</div>;
  if (!user) return <LoginForm onLogin={setUser} />;
  return <>{children(user, logout)}</>;
}

function LoginForm({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await apiFetch<{ token: string; user: User }>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(result.token);
      onLogin(result.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <h1>Document Assistant</h1>
        <label>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
