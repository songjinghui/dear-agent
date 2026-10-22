import { useState } from "react";
import type { ImageAttachment } from "./types";

export function ImageAttachments({ items }: { items?: ImageAttachment[] }) {
  const [active, setActive] = useState<ImageAttachment | null>(null);
  if (!items?.length) return null;
  return <>
    <div className="message-attachments">
      {items.map((item) => <button className="message-attachment" key={item.id} type="button" onClick={() => setActive(item)} aria-label={`查看图片：${item.filename}`}>
        <img src={item.url} alt={item.filename} loading="lazy" />
        <span>{item.filename}</span>
      </button>)}
    </div>
    {active && <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={active.filename} onMouseDown={(event) => { if (event.target === event.currentTarget) setActive(null); }}>
      <button className="image-lightbox-close" type="button" onClick={() => setActive(null)} aria-label="关闭图片">×</button>
      <figure><img src={active.url} alt={active.filename} /><figcaption>{active.filename}</figcaption></figure>
    </div>}
  </>;
}
