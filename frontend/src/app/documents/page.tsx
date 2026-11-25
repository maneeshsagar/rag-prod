"use client";

// Documents page: upload files, follow each one through the processing stages,
// stop processing, and (admins) delete documents.

import Link from "next/link";
import { ChangeEvent, useCallback, useEffect, useState } from "react";
import AuthGate from "@/components/AuthGate";
import IngestionStages from "@/components/IngestionStages";
import { apiFetch } from "@/lib/api";
import type { DocumentInfo, User } from "@/lib/types";

const ACCEPT = ".pdf,.docx,.pptx,.html,.htm,.md";
const REFRESH_MS = 2000;

export default function DocumentsPage() {
  return <AuthGate>{(user) => <DocumentList user={user} />}</AuthGate>;
}

function DocumentList({ user }: { user: User }) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [message, setMessage] = useState("");

  const load = useCallback(() => {
    apiFetch<DocumentInfo[]>("/documents").then(setDocuments).catch(() => undefined);
  }, []);

  // Refresh every few seconds so stage changes show up while documents are processed.
  useEffect(() => {
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    for (const file of files) {
      const form = new FormData();
      form.append("file", file);
      try {
        await apiFetch("/documents", { method: "POST", body: form });
        setMessage(`Uploaded ${file.name}; processing…`);
      } catch (err) {
        setMessage(`${file.name}: ${err instanceof Error ? err.message : "upload failed"}`);
      }
      load();
    }
  }

  async function act(path: string, method: string, done: string) {
    try {
      await apiFetch(path, { method });
      setMessage(done);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Request failed");
    }
    load();
  }

  return (
    <div className="page">
      <header className="page-head">
        <Link href="/">← Back to chat</Link>
        <h1>Documents</h1>
        <label className="primary upload">
          Upload files
          <input type="file" accept={ACCEPT} multiple onChange={upload} hidden />
        </label>
      </header>
      {message && <p className="muted">{message}</p>}

      {documents.length === 0 && (
        <p className="muted">No documents yet. Upload some, or run `make ingest`.</p>
      )}

      <div className="doc-list">
        {documents.map((doc) => (
          <DocumentCard
            key={doc.id}
            doc={doc}
            isAdmin={user.role === "admin"}
            onStop={() => act(`/documents/${doc.id}/stop`, "POST", `Stopping ${doc.source_name}…`)}
            onDelete={() => act(`/documents/${doc.id}`, "DELETE", `Deleted ${doc.source_name}`)}
          />
        ))}
      </div>
    </div>
  );
}

type CardProps = { doc: DocumentInfo; isAdmin: boolean; onStop: () => void; onDelete: () => void };

function DocumentCard({ doc, isAdmin, onStop, onDelete }: CardProps) {
  const [confirming, setConfirming] = useState(false);
  const processing = doc.status === "pending";
  const stopping = doc.job?.stopping ?? false;

  return (
    <div className="doc-card">
      <div className="doc-head">
        <div className="doc-title">
          <strong>{doc.title}</strong>
          <div className="muted small">
            {doc.source_name} · {doc.file_format.toUpperCase()}
            {doc.page_count ? ` · ${doc.page_count} pages` : ""}
            {doc.chunk_count ? ` · ${doc.chunk_count} chunks` : ""}
          </div>
        </div>
        <span className={`badge ${doc.status}`}>{stopping ? "stopping" : doc.status}</span>

        {processing && (
          <button onClick={onStop} disabled={stopping}>{stopping ? "Stopping…" : "Stop"}</button>
        )}
        {isAdmin && !processing && !confirming && (
          <button onClick={() => setConfirming(true)}>Delete</button>
        )}
        {confirming && (
          <>
            <button className="danger" onClick={onDelete}>Confirm delete</button>
            <button onClick={() => setConfirming(false)}>Cancel</button>
          </>
        )}
      </div>

      <IngestionStages doc={doc} />

      {doc.summary && <p className="small summary">{doc.summary}</p>}
      {doc.warnings.map((w) => <div key={w} className="warning small">⚠ {w}</div>)}
      {doc.error && <div className="error small">{doc.error}</div>}
      {doc.status === "cancelled" && (
        <div className="muted small">Processing was stopped. Upload the file again to retry.</div>
      )}
    </div>
  );
}
