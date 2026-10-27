import { Fragment, type ReactNode } from "react";
import { parseMarkdownBlocks, tokenizeInlineMarkdown } from "./markdown";

function InlineMarkdown({ text }: { text: string }) {
  return <>{tokenizeInlineMarkdown(text).map((token, index) => {
    const key = `${token.type}-${index}`;
    if (token.type === "strong") return <strong key={key}>{token.content}</strong>;
    if (token.type === "emphasis") return <em key={key}>{token.content}</em>;
    if (token.type === "code") return <code key={key}>{token.content}</code>;
    if (token.type === "strike") return <del key={key}>{token.content}</del>;
    if (token.type === "link") {
      return token.href
        ? <a key={key} href={token.href} target="_blank" rel="noreferrer noopener">{token.content}</a>
        : <Fragment key={key}>{token.content}</Fragment>;
    }
    return <Fragment key={key}>{token.content}</Fragment>;
  })}</>;
}

export function MarkdownMessage({ content }: { content: string }) {
  const blocks = parseMarkdownBlocks(content);
  return <div className="message-markdown">{blocks.map((block, index): ReactNode => {
    const key = `${block.type}-${index}`;
    if (block.type === "heading") {
      if (block.level === 1) return <h1 key={key}><InlineMarkdown text={block.text} /></h1>;
      if (block.level === 2) return <h2 key={key}><InlineMarkdown text={block.text} /></h2>;
      if (block.level === 3) return <h3 key={key}><InlineMarkdown text={block.text} /></h3>;
      return <h4 key={key}><InlineMarkdown text={block.text} /></h4>;
    }
    if (block.type === "unordered-list") {
      return <ul key={key}>{block.items.map((item, itemIndex) => <li key={itemIndex}><InlineMarkdown text={item} /></li>)}</ul>;
    }
    if (block.type === "ordered-list") {
      return <ol key={key} start={block.start}>{block.items.map((item, itemIndex) => <li key={itemIndex}><InlineMarkdown text={item} /></li>)}</ol>;
    }
    if (block.type === "quote") return <blockquote key={key}><InlineMarkdown text={block.text} /></blockquote>;
    if (block.type === "code") return <pre key={key}><code data-language={block.language || undefined}>{block.content}</code></pre>;
    if (block.type === "table") {
      return <div className="markdown-table-wrap" key={key}><table><thead><tr>{block.headers.map((header, cellIndex) => <th key={cellIndex}><InlineMarkdown text={header} /></th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{block.headers.map((_header, cellIndex) => <td key={cellIndex}><InlineMarkdown text={row[cellIndex] || ""} /></td>)}</tr>)}</tbody></table></div>;
    }
    if (block.type === "rule") return <hr key={key} />;
    return <p key={key}><InlineMarkdown text={block.text} /></p>;
  })}</div>;
}
