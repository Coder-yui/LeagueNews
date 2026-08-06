"use client";

import { useState } from "react";
import { adminApi } from "@/lib/api";
import type { EventReviewTask, ReviewTask } from "@/lib/types";

type ReviewKind = "message" | "event" | "ocr";

function proposalNumber(
  proposal: Record<string, unknown>,
  key: string,
): string {
  return typeof proposal[key] === "number" ? String(proposal[key]) : "";
}

export function ReviewCard({
  review,
  kind,
  onResolved,
}: {
  review: ReviewTask | EventReviewTask;
  kind: ReviewKind;
  onResolved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [contentType, setContentType] = useState(
    typeof review.proposal.content_type === "string"
      ? review.proposal.content_type
      : "",
  );
  const [credibility, setCredibility] = useState(
    proposalNumber(review.proposal, "credibility_score"),
  );
  const [importance, setImportance] = useState(
    proposalNumber(review.proposal, "importance_score"),
  );
  const [eventDraft, setEventDraft] = useState(
    JSON.stringify(review.proposal, null, 2),
  );
  const base =
    kind === "event" ? "/event-workflows/reviews" : "/workflows/reviews";

  const act = async (action: "approve" | "reject") => {
    setBusy(action);
    setError(null);
    try {
      const body =
        action === "approve"
          ? { note: note || null }
          : kind === "event"
            ? { reason: note || "管理台拒绝该建议" }
            : {
                feedback_type: "analysis_correction",
                reason: note || "管理台拒绝该建议",
                corrected_values: {},
              };
      await adminApi(`${base}/${review.id}/${action}`, {
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

  const correctAndApprove = async () => {
    setBusy("correct");
    setError(null);
    try {
      const body =
        kind === "event"
          ? {
              decision_draft: JSON.parse(eventDraft) as Record<string, unknown>,
              note: note || null,
            }
          : {
              content_type: contentType || null,
              credibility_score:
                credibility === "" ? null : Number(credibility),
              importance_score: importance === "" ? null : Number(importance),
              note: note || null,
            };
      await adminApi(`${base}/${review.id}/correct-and-approve`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      onResolved();
    } catch (value) {
      setError(
        value instanceof Error
          ? value.message
          : "修正提交失败，请检查 JSON 和字段范围",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="admin-review-card">
      <header>
        <div>
          <span>
            {kind === "event"
              ? "事件归属"
              : kind === "ocr"
                ? "OCR 待验"
                : "消息分析"}
          </span>
          <strong>审核 #{review.id}</strong>
        </div>
        <b>{"stage" in review ? review.stage : "event_decision"}</b>
      </header>
      <div className="admin-review-proposal">
        <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
      </div>
      {editing &&
        (kind === "event" ? (
          <label className="admin-review-note">
            修改归属决策 JSON
            <textarea
              value={eventDraft}
              onChange={(event) => setEventDraft(event.target.value)}
            />
          </label>
        ) : (
          <div className="admin-correction-grid">
            <label>
              content_type
              <input
                value={contentType}
                onChange={(event) => setContentType(event.target.value)}
              />
            </label>
            <label>
              可信度 0–1
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={credibility}
                onChange={(event) => setCredibility(event.target.value)}
              />
            </label>
            <label>
              重要性 0–1
              <input
                type="number"
                min="0"
                max="1"
                step="0.01"
                value={importance}
                onChange={(event) => setImportance(event.target.value)}
              />
            </label>
          </div>
        ))}
      {editing && (
        <label className="admin-review-note">
          修正/退回备注
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="说明判断依据或需要修正的内容"
          />
        </label>
      )}
      <div className="admin-inline-actions">
        <button
          type="button"
          onClick={() => void act("approve")}
          disabled={busy !== null}
        >
          {busy === "approve"
            ? "批准中…"
            : kind === "event"
              ? "批准建议"
              : "批准"}
        </button>
        <button type="button" onClick={() => setEditing((value) => !value)}>
          {editing ? "收起修正" : kind === "event" ? "修改归属" : "修正后批准"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => void correctAndApprove()}
            disabled={busy !== null}
          >
            {busy === "correct" ? "提交中…" : "提交修正并批准"}
          </button>
        )}
        <button
          className="danger"
          type="button"
          onClick={() => void act("reject")}
          disabled={busy !== null}
        >
          {busy === "reject" ? "拒绝中…" : "拒绝"}
        </button>
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
    </article>
  );
}
