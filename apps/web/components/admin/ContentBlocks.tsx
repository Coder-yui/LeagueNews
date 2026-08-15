import Image from "next/image";
import { imageBlockSrcWithRemoteFallback } from "@/lib/image-src";
import type { ContentBlock } from "@/lib/types";

export function ContentBlocks({ blocks }: { blocks: ContentBlock[] }) {
  if (!blocks.length) return <p className="admin-muted">没有可显示的内容块。</p>;
  return <div className="admin-content-blocks">{blocks.map((block, index) => { const key = block.id ?? `${block.type}-${index}`; if (block.type === "image") { const src = imageBlockSrcWithRemoteFallback(block); return src ? <figure key={key}><Image src={src} alt={block.alt_text ?? block.caption ?? "原文图片"} width={960} height={640} unoptimized referrerPolicy="no-referrer" /><figcaption>{block.caption ?? "原文图片"}</figcaption></figure> : null; } if (block.type === "heading") return <h3 key={key}>{block.text}</h3>; if (block.type === "quote") return <blockquote key={key}>{block.text}</blockquote>; if (block.type === "list") return block.ordered ? <ol key={key}>{block.items?.map((item) => <li key={item}>{item}</li>)}</ol> : <ul key={key}>{block.items?.map((item) => <li key={item}>{item}</li>)}</ul>; if (block.type === "embed") return <a key={key} className="admin-embed" href={block.source_url} target="_blank" rel="noreferrer">嵌入内容 · {block.embed_kind ?? "external"}</a>; return <p key={key}>{block.text}</p>; })}</div>;
}
