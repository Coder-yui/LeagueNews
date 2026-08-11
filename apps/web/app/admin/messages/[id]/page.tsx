import Link from "next/link";
import { notFound } from "next/navigation";
import { ContentBlocks } from "@/components/admin/ContentBlocks";
import { ImportanceDimensions } from "@/components/admin/ImportanceDimensions";
import { MessageActions } from "@/components/admin/MessageActions";
import { adminApi } from "@/lib/api";
import type { PublishedItem } from "@/lib/types";

export default async function MessageDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const item = await adminApi<PublishedItem>(
    `/normalized-items/${id}/published`,
  ).catch(() => null);
  if (!item) notFound();

  return (
    <div className="admin-page admin-detail-page">
      <Link className="admin-back" href="/admin/messages">← 返回消息管理</Link>
      <header className="admin-detail-head">
        <div className="admin-badge-row">
          <span className="admin-badge">消息 #{item.id}</span>
          <span className="admin-badge">{item.message_type}</span>
          {item.products.map((product) => (
            <span className="admin-badge subtle" key={product}>{product}</span>
          ))}
          <span className="admin-badge success">published</span>
        </div>
        <h1>{item.title}</h1>
        <p>{item.summary}</p>
        <div className="admin-badge-row">
          {item.topics.map((topic) => (
            <span className="admin-badge subtle" key={topic}>{topic}</span>
          ))}
        </div>
        <dl>
          <div><dt>来源</dt><dd>{item.source_name}</dd></div>
          <div><dt>作者</dt><dd>{item.author ?? "未知"}</dd></div>
          <div><dt>发布时间</dt><dd>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "未知"}</dd></div>
          <div><dt>内容形式</dt><dd>{item.content_form}</dd></div>
          <div><dt>语言</dt><dd>{item.source_language ?? "未知"} → zh-CN</dd></div>
        </dl>
      </header>
      <section className="admin-detail-section">
        <h2>原文内容</h2>
        <ContentBlocks blocks={item.original_content_blocks} />
      </section>
      {item.translated_content_blocks.length > 0 && (
        <section className="admin-detail-section">
          <h2>翻译结果</h2>
          <ContentBlocks blocks={item.translated_content_blocks} />
        </section>
      )}
      <section className="admin-detail-section">
        <h2>重要性与依据</h2>
        <div className="admin-detail-breakdowns">
          <ImportanceDimensions
            scoreValue={item.importance_score}
            dimensions={item.importance_dimensions}
          />
        </div>
      </section>
      <section className="admin-detail-section">
        <h2>操作</h2>
        <MessageActions itemId={item.id} />
      </section>
    </div>
  );
}
