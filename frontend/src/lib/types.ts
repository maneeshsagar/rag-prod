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
  stage: string; // store | parse | chunk | embed | index | summarize | done
  status: "running" | "done" | "failed" | "cancelled";
  progress: string | null; // e.g. "200/450 chunks"
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
  job: IngestionJob | null;
};
