import Link from "next/link";
import { notFound } from "next/navigation";
import { adminApi } from "@/lib/api";
import type { PublishedItem } from "@/lib/types";
import { ContentBlocks } from "@/components/admin/ContentBlocks";
import { ImportanceDimensions } from "@/components/admin/ImportanceDimensions";
import { MessageActions } from "@/components/admin/MessageActions";

export default async function MessageDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const item = await adminApi<PublishedItem>(`/normalized-items/${id}/published`).catch(() => null);
  if (!item) notFound();
  return <div className="admin-page admin-detail-page">
    <Link className="admin-back" href="/admin/messages">← 返回消息管理</Link>
    <header className="admin-detail-head"><div className="admin-badge-row"><span className="admin-badge">{item.content_type ?? "null"}</span><span className="admin-badge subtle">{item.primary_topic}</span><span className="admin-badge success">published</span></div><h1>{item.title}</h1><p>{item.summary}</p><dl><div><dt>来源</dt><dd>{item.source_name}</dd></div><div><dt>作者</dt><dd>{item.author ?? "未知"}</dd></div><div><dt>发布时间</dt><dd>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "未知"}</dd></div><div><dt>语言</dt><dd>{item.source_language ?? "未知"} → zh-CN</dd></div></dl></header>
    <section className="admin-detail-section"><h2>原文内容</h2><ContentBlocks blocks={item.original_content_blocks} /></section>
    {item.translated_content_blocks.length > 0 && <section className="admin-detail-section"><h2>翻译结果</h2><ContentBlocks blocks={item.translated_content_blocks} /></section>}
    <section className="admin-detail-section"><h2>重要性与依据</h2><div className="admin-detail-breakdowns"><ImportanceDimensions scoreValue={item.importance_score} dimensions={item.importance_dimensions} /></div></section>
    <section className="admin-detail-section"><h2>Fact Claims</h2><div className="admin-claim-list">{item.fact_claims?.map((claim) => <article key={claim.id}><span>{claim.stance}</span><strong>{JSON.stringify(claim.subject)} · {claim.predicate} · {JSON.stringify(claim.object_value)}</strong><p>归因：{JSON.stringify(claim.attribution)}</p></article>)}{!item.fact_claims?.length && <p className="admin-muted">没有活跃断言。</p>}</div></section>
    <section className="admin-detail-section"><h2>归属事件</h2><div className="admin-membership-list">{item.event_memberships?.map((membership) => <Link href={`/admin/events/${membership.event_id}`} key={`${membership.event_id}-${membership.membership_role}`}><span className="admin-badge">{membership.membership_role}</span><strong>{membership.event_title}</strong><small>{membership.event_type} · {membership.evidence_stance}</small></Link>)}{!item.event_memberships?.length && <p className="admin-muted">尚未归属事件。</p>}</div></section>
    <section className="admin-detail-section"><h2>操作</h2><MessageActions itemId={item.id} /></section>
  </div>;
}
