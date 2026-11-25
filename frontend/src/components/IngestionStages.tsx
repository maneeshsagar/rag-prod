"use client";

// Step-by-step progress of one document:
// Uploaded → Parsing → Chunking → Embedding → Indexing → Summarizing → Done

import type { DocumentInfo } from "@/lib/types";

const STAGES: { key: string; label: string }[] = [
  { key: "store", label: "Uploaded" },
  { key: "parse", label: "Parsing" },
  { key: "chunk", label: "Chunking" },
  { key: "embed", label: "Embedding" },
  { key: "index", label: "Indexing" },
  { key: "summarize", label: "Summarizing" },
  { key: "done", label: "Done" },
];

type StepState = "done" | "current" | "stopped" | "todo";

function stepStates(doc: DocumentInfo): StepState[] {
  if (doc.status === "indexed") return STAGES.map(() => "done");
  const current = Math.max(0, STAGES.findIndex((s) => s.key === doc.job?.stage));
  const ended = doc.status === "failed" || doc.status === "cancelled";
  return STAGES.map((_, i) => {
    if (i < current) return "done";
    if (i === current) return ended ? "stopped" : "current";
    return "todo";
  });
}

export default function IngestionStages({ doc }: { doc: DocumentInfo }) {
  const states = stepStates(doc);
  return (
    <ol className="stages" aria-label="Processing stages">
      {STAGES.map((stage, i) => (
        <li key={stage.key} className={`stage ${states[i]}`}>
          <span className="dot" />
          {stage.label}
          {states[i] === "current" && stage.key === "embed" && doc.job?.progress && (
            <span className="muted"> ({doc.job.progress})</span>
          )}
        </li>
      ))}
    </ol>
  );
}
