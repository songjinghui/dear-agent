export type MarkdownBlock =
  | { type: "paragraph"; text: string }
  | { type: "heading"; level: number; text: string }
  | { type: "unordered-list"; items: string[] }
  | { type: "ordered-list"; items: string[]; start: number }
  | { type: "quote"; text: string }
  | { type: "code"; language: string; content: string }
  | { type: "table"; headers: string[]; rows: string[][] }
  | { type: "rule" };

export type InlineToken =
  | { type: "text"; content: string }
  | { type: "strong" | "emphasis" | "code" | "strike"; content: string }
  | { type: "link"; content: string; href: string | null };

const orderedList = /^(\d+)[.)]\s+(.+)$/;
const unorderedList = /^[-+*]\s+(.+)$/;
const tableDivider = /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/;
const inlinePattern = /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)|(\*\*[^*]+?\*\*|__[^_]+?__)|`([^`]+)`|~~([^~]+)~~|\*([^*\n]+)\*|_([^_\n]+)_/g;

function isTableRow(line: string) {
  return line.includes("|");
}

function tableCells(line: string) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function isRule(line: string) {
  return /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line);
}

function startsBlock(lines: string[], index: number) {
  const line = lines[index] || "";
  return /^```/.test(line)
    || /^#{1,6}\s+/.test(line)
    || /^>\s?/.test(line)
    || unorderedList.test(line)
    || orderedList.test(line)
    || isRule(line)
    || (isTableRow(line) && tableDivider.test(lines[index + 1] || ""));
}

export function parseMarkdownBlocks(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];

  for (let index = 0; index < lines.length;) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^```\s*([^`]*)$/);
    if (fence) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) code.push(lines[index++]);
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", language: fence[1].trim(), content: code.join("\n") });
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, text: heading[2] });
      index += 1;
      continue;
    }

    if (isRule(line)) {
      blocks.push({ type: "rule" });
      index += 1;
      continue;
    }

    if (isTableRow(line) && tableDivider.test(lines[index + 1] || "")) {
      const headers = tableCells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && isTableRow(lines[index])) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      blocks.push({ type: "table", headers, rows });
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) quote.push(lines[index++].replace(/^>\s?/, ""));
      blocks.push({ type: "quote", text: quote.join("\n") });
      continue;
    }

    const unordered = line.match(unorderedList);
    if (unordered) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].match(unorderedList);
        if (!item) break;
        items.push(item[1]);
        index += 1;
      }
      blocks.push({ type: "unordered-list", items });
      continue;
    }

    const ordered = line.match(orderedList);
    if (ordered) {
      const items: string[] = [];
      const start = Number(ordered[1]);
      while (index < lines.length) {
        const item = lines[index].match(orderedList);
        if (!item) break;
        items.push(item[2]);
        index += 1;
      }
      blocks.push({ type: "ordered-list", items, start });
      continue;
    }

    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && (paragraph.length === 0 || !startsBlock(lines, index))) {
      paragraph.push(lines[index]);
      index += 1;
    }
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
  }

  return blocks;
}

export function safeMarkdownHref(value: string): string | null {
  const href = value.trim();
  return /^(https?:\/\/|mailto:)/i.test(href) ? href : null;
}

export function tokenizeInlineMarkdown(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let lastIndex = 0;
  inlinePattern.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = inlinePattern.exec(text))) {
    if (match.index > lastIndex) tokens.push({ type: "text", content: text.slice(lastIndex, match.index) });
    if (match[1]) tokens.push({ type: "link", content: match[1], href: safeMarkdownHref(match[2]) });
    else if (match[3]) tokens.push({ type: "strong", content: match[3].slice(2, -2) });
    else if (match[4]) tokens.push({ type: "code", content: match[4] });
    else if (match[5]) tokens.push({ type: "strike", content: match[5] });
    else tokens.push({ type: "emphasis", content: match[6] || match[7] });
    lastIndex = inlinePattern.lastIndex;
  }
  if (lastIndex < text.length) tokens.push({ type: "text", content: text.slice(lastIndex) });
  return tokens;
}
