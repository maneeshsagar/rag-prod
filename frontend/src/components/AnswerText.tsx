"use client";

// Renders an answer's light Markdown: paragraphs, bullet / numbered lists, headings,
// **bold**, and "[2]" citation markers as clickable chips.

import { ReactNode } from "react";

type Props = { text: string; onCitationClick: (n: number) => void };

const CITATION = /(\[\d+(?:\s*[,;]\s*\d+)*\])/; // capture group keeps matches in split()
const BOLD = /(\*\*[^*]+\*\*)/;
const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;
const HEADING = /^\s*#{1,6}\s+/;

function inline(text: string, onCitationClick: (n: number) => void, key: string): ReactNode[] {
  // split() with a capture group returns [text, match, text, match, ...]: odd items are citations.
  return text.split(CITATION).map((part, i) => {
    if (i % 2 === 1) {
      const numbers = part.slice(1, -1).split(/\s*[,;]\s*/).map(Number);
      return numbers.map((n) => (
        <button key={`${key}-${i}-${n}`} className="cite" onClick={() => onCitationClick(n)}>{n}</button>
      ));
    }
    return part.split(BOLD).map((piece, j) =>
      j % 2 === 1
        ? <strong key={`${key}-${i}-${j}`}>{piece.slice(2, -2)}</strong>
        : <span key={`${key}-${i}-${j}`}>{piece}</span>,
    );
  });
}

type Block = { kind: "p" | "ul" | "ol" | "h"; lines: string[] };

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      blocks.push({ kind: "p", lines: [] }); // blank line ends the current block
      continue;
    }
    const kind = BULLET.test(line) ? "ul" : NUMBERED.test(line) ? "ol" : HEADING.test(line) ? "h" : "p";
    const content = line.replace(BULLET, "").replace(NUMBERED, "").replace(HEADING, "");
    const last = blocks[blocks.length - 1];
    if (last && last.kind === kind && kind !== "h" && last.lines.length > 0) last.lines.push(content);
    else blocks.push({ kind, lines: [content] });
  }
  return blocks.filter((b) => b.lines.length > 0);
}

export default function AnswerText({ text, onCitationClick }: Props) {
  return (
    <>
      {toBlocks(text).map((block, b) => {
        const key = `b${b}`;
        if (block.kind === "ul" || block.kind === "ol") {
          const List = block.kind;
          return (
            <List key={key}>
              {block.lines.map((line, i) => <li key={i}>{inline(line, onCitationClick, `${key}-${i}`)}</li>)}
            </List>
          );
        }
        if (block.kind === "h") return <h4 key={key}>{inline(block.lines[0], onCitationClick, key)}</h4>;
        return <p key={key}>{inline(block.lines.join(" "), onCitationClick, key)}</p>;
      })}
    </>
  );
}
