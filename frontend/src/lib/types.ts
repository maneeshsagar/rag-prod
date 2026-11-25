// Shapes of the data the backend API returns.

export type User = { id: string; email: string; name: string; role: string };

export type SourceInfo = {
  number: number;
  doc_id: string;
  title: string;
  citation_label: string; // "p. 12", "slide 4", "§ Parental leave"
  file_format: string;
  section_path: string;
  score: number;
  version_note?: string | null; // "older version; newer: ..." or "copy of ..."
};

export type Citation = SourceInfo & {
  quote: string; // best-matching sentence from the source
  supported: boolean; // the cited sentence shares key words with the source
};

export type ChatMessage = {
  id?: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  sources?: SourceInfo[];
  route?: string;
  status?: string; // progress text while the answer is being prepared
  error?: string;
  streaming?: boolean;
  serverPending?: boolean; // the server is still writing this answer (we are not streaming it)
  notFound?: boolean;
};

export type ConversationSummary = { id: string; title: string; updated_at: string };

export type IngestionJob = {
  stage: string; // queued | parse | chunk | embed | index | summarize | done
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  progress: string | null; // e.g. "200/450 chunks"
  error: string | null; // last error (a queued job with an error is waiting to retry)
  attempts: number;
  stopping: boolean; // Stop was pressed; waiting for the next checkpoint
};

export type DocumentInfo = {
  id: string;
  title: string;
  source_name: string;
  file_format: string;
  page_count: number | null;
  status: "pending" | "indexed" | "failed" | "cancelled";
  chunk_count: number | null;
  warnings: string[];
  error: string | null;
  summary: string | null;
  created_at: string;
  effective_date: string | null; // date the document states
  is_latest: boolean;
  version_confidence: "explicit" | "inferred" | "duplicate" | null;
  newer_version: { id: string; title: string; source_name: string } | null;
  job: IngestionJob | null;
};

// Counts for the whole ingestion queue (GET /documents/queue).
export type QueueSummary = {
  queued: number;
  running: number;
  done: number;
  failed: number;
  cancelled: number;
  done_last_hour: number;
  avg_seconds_per_doc: number | null;
};
