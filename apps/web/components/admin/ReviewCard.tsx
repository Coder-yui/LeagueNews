"use client";

import { useState } from "react";
import { adminApi } from "@/lib/api";
import type { ReviewTask } from "@/lib/types";

const stageLabels: Record<string, string> = {
  relevance: "相关性",
  translation: "翻译",
  message_analysis: "消息分析",
  importance: "重要性",
};

const products = [
  "lol_pc",
  "tft",
  "lol_esports",
  "lol_universe",
  "other_lol_product",
  "riot_ecosystem",
  "unknown",
];

const contentForms = ["original", "repost", "quote", "media_only", "link_only"];

const messageTypes = [
  "game_patch_notes",
  "game_official_preview",
  "game_announcement",
  "game_notice",
  "game_promotion_interaction",
  "game_community_notice",
  "game_community_promotion_interaction",
  "game_leak",
  "game_community_discussion",
  "esports_announcement",
  "esports_promotion_interaction",
  "esports_rumor_speculation",
  "esports_community_discussion",
  "lol_universe_announcement",
  "lol_universe_promotion_interaction",
  "lol_universe_leak",
  "lol_universe_community_discussion",
  "other_lol_product_announcement",
  "other_lol_product_promotion_interaction",
  "other_lol_product_leak",
  "other_lol_product_community_discussion",
  "riot_ecosystem_announcement",
  "riot_ecosystem_promotion_interaction",
  "riot_ecosystem_leak",
  "riot_ecosystem_community_discussion",
  "unknown",
];

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function splitList(value: string): string[] {
  return value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean);
}

export function ReviewCard({
  review,
  onResolved,
}: {
  review: ReviewTask;
  onResolved: () => void;
}) {
  const { stage, proposal } = review;
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [decision, setDecision] = useState(
    typeof proposal.decision === "string" ? proposal.decision : "uncertain",
  );
  const [selectedProducts, setSelectedProducts] = useState(
    stringList(proposal.products).join(", "),
  );
  const [contentForm, setContentForm] = useState(
    typeof proposal.content_form === "string" ? proposal.content_form : "original",
  );
  const [messageType, setMessageType] = useState(
    typeof proposal.message_type === "string" ? proposal.message_type : "unknown",
  );
  const [topics, setTopics] = useState(stringList(proposal.topics).join(", "));
  const [importance, setImportance] = useState(
    typeof proposal.importance_score === "number"
      ? String(Math.round(proposal.importance_score * 100))
      : "",
  );

  const request = async (path: string, body: Record<string, unknown>, action: string) => {
    setBusy(action);
    setError(null);
    try {
      await adminApi(`/workflows/reviews/${review.id}/${path}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      onResolved();
    } catch (value) {
      setError(value instanceof Error ? value.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  const approve = () => request("approve", { note: note || null }, "approve");
  const reject = () =>
    request(
      "reject",
      {
        feedback_type:
          stage === "translation" ? "translation_correction" : "analysis_correction",
        reason: note || "管理台退回该审核结果",
        corrected_values: {},
        glossary_updates: [],
      },
      "reject",
    );

  const correctAndApprove = () => {
    const body: Record<string, unknown> = { note: note || null };
    if (stage === "relevance") body.decision = decision;
    if (stage === "message_analysis") {
      body.products = splitList(selectedProducts);
      body.content_form = contentForm;
    }
    if (stage === "importance") {
      body.message_type = messageType;
      body.topics = splitList(topics);
      if (importance !== "") body.importance_score = Number(importance) / 100;
    }
    return request("correct-and-approve", body, "correct");
  };

  return (
    <article className="admin-review-card stage-review-card">
      <header>
        <div>
          <span>消息处理</span>
          <strong>审核 #{review.id}</strong>
        </div>
        <b>{stageLabels[stage] ?? stage}</b>
      </header>

      <div className="review-content-panel">
        {typeof proposal.title === "string" && <h3>{proposal.title}</h3>}
        {typeof proposal.summary === "string" && <p>{proposal.summary}</p>}
        <dl>
          {typeof proposal.decision === "string" && <div><dt>相关性</dt><dd>{proposal.decision}</dd></div>}
          {Array.isArray(proposal.products) && <div><dt>产品</dt><dd>{stringList(proposal.products).join(" · ")}</dd></div>}
          {typeof proposal.content_form === "string" && <div><dt>内容形式</dt><dd>{proposal.content_form}</dd></div>}
          {typeof proposal.message_type === "string" && <div><dt>消息类型</dt><dd>{proposal.message_type}</dd></div>}
          {Array.isArray(proposal.topics) && <div><dt>主题</dt><dd>{stringList(proposal.topics).join(" · ")}</dd></div>}
          {typeof proposal.importance_score === "number" && <div><dt>重要性</dt><dd>{Math.round(proposal.importance_score * 100)}</dd></div>}
          {typeof proposal.translation_status === "string" && <div><dt>翻译状态</dt><dd>{proposal.translation_status}</dd></div>}
        </dl>
      </div>

      <details className="review-json-details">
        <summary>查看完整审核草稿 JSON</summary>
        <pre>{JSON.stringify(proposal, null, 2)}</pre>
      </details>

      {editing && stage === "relevance" && (
        <label>相关性
          <select value={decision} onChange={(event) => setDecision(event.target.value)}>
            <option value="relevant">relevant</option>
            <option value="uncertain">uncertain</option>
            <option value="irrelevant">irrelevant</option>
          </select>
        </label>
      )}
      {editing && stage === "message_analysis" && (
        <div className="admin-correction-grid">
          <label>产品（逗号分隔）
            <input list="product-options" value={selectedProducts} onChange={(event) => setSelectedProducts(event.target.value)} />
            <datalist id="product-options">{products.map((value) => <option value={value} key={value} />)}</datalist>
          </label>
          <label>内容形式
            <select value={contentForm} onChange={(event) => setContentForm(event.target.value)}>
              {contentForms.map((value) => <option value={value} key={value}>{value}</option>)}
            </select>
          </label>
        </div>
      )}
      {editing && stage === "importance" && (
        <div className="admin-correction-grid">
          <label>消息类型
            <select value={messageType} onChange={(event) => setMessageType(event.target.value)}>
              {messageTypes.map((value) => <option value={value} key={value}>{value}</option>)}
            </select>
          </label>
          <label>主题（逗号分隔）
            <input value={topics} onChange={(event) => setTopics(event.target.value)} />
          </label>
          <label>重要性 0-100
            <input type="number" min="0" max="100" value={importance} onChange={(event) => setImportance(event.target.value)} />
          </label>
        </div>
      )}
      {editing && (
        <label className="admin-review-note">备注
          <textarea value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
      )}

      <div className="admin-inline-actions">
        <button type="button" onClick={() => void approve()} disabled={busy !== null}>
          {busy === "approve" ? "批准中..." : stage === "importance" ? "批准并发布消息" : "批准并进入下一阶段"}
        </button>
        <button type="button" onClick={() => setEditing((value) => !value)}>
          {editing ? "收起操作" : "修正或退回"}
        </button>
        {editing && ["relevance", "message_analysis", "importance"].includes(stage) && (
          <button type="button" onClick={() => void correctAndApprove()} disabled={busy !== null}>
            {busy === "correct" ? "提交中..." : "提交修正并批准"}
          </button>
        )}
        {editing && (
          <button className="danger" type="button" onClick={() => void reject()} disabled={busy !== null}>
            {busy === "reject" ? "退回中..." : "退回"}
          </button>
        )}
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
    </article>
  );
}
