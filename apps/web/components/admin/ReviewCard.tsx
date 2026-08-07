"use client";

import { useState, type ReactNode } from "react";
import { adminApi } from "@/lib/api";
import type { EventReviewTask, ReviewTask } from "@/lib/types";

type ReviewKind = "message" | "event";
type Proposal = Record<string, unknown>;

const stageLabels: Record<string, string> = {
  relevance: "相关性与产品范围审核",
  translation: "翻译与术语审核",
  fact_classify: "事实抽取与分类审核",
  importance: "重要性审核",
  claim_gen: "事实断言审核",
  event_decision: "事件归属审核",
};

const correctionOptions = {
  sourceKind: ["first_party", "attributed_report", "data_mined", "community", "unknown"],
  informationStage: ["announcement", "preview", "active", "result", "update", "correction", "rumor", "speculation", "reminder", "commentary"],
  contentForm: ["original", "repost", "roundup"],
  eventAssertion: ["asserted", "speculative", "negated", "context_only"],
  primaryTopic: ["patch", "esports", "roster", "champion", "skin", "activity", "game_mode", "tft", "commerce", "service", "community", "business", "universe", "media", "other"],
  subtopic: ["patch_notes", "patch_preview", "hotfix", "pbe_change", "champion_release", "champion_update", "item_rune_system", "game_mode_release", "game_mode_update", "tft_set", "tft_patch", "skin_release", "tft_cosmetic", "shop_rotation", "shop_offer", "free_rotation", "free_reward", "event_pass", "in_game_activity", "ticketing", "match_schedule", "match_result", "standings_qualification", "roster_move", "disciplinary", "maintenance", "outage", "security", "merch", "partnership", "corporate", "lore", "music_video", "community_event", "gameplay_guide", "community_post", "other"],
  productScope: ["lol_pc", "lol_esports", "tft", "lol_universe", "riot_corporate", "lol_merch_music", "wild_rift", "2xko", "unrelated", "uncertain"],
} as const;

const dimensionLabels: Record<string, string> = {
  editorial_subtype: "编辑类型",
  scale: "内容规模",
  audience_region: "适用范围",
  competition_region: "赛事区域",
  prominence: "对象知名度",
  skin_tier: "皮肤档次",
  information_value: "信息增量",
};

function objectValue(value: unknown): Proposal {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Proposal)
    : {};
}

function objectList(value: unknown): Proposal[] {
  return Array.isArray(value)
    ? value.filter(
        (entry): entry is Proposal =>
          Boolean(entry) && typeof entry === "object" && !Array.isArray(entry),
      )
    : [];
}

function textValue(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "—";
}

function textList(value: unknown): string {
  return Array.isArray(value)
    ? value.map(textValue).filter((entry) => entry !== "—").join("、") || "—"
    : "—";
}

function scoreValue(value: unknown, maximum = 1): string {
  if (typeof value !== "number") return "—";
  return maximum === 1
    ? `${Math.round(value * 100)} / 100`
    : `${value} / ${maximum}`;
}

function structuredValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(structuredValue).filter(Boolean).join("、");
  }
  if (value && typeof value === "object") {
    return Object.entries(value as Proposal)
      .map(([key, entry]) => `${key}：${structuredValue(entry)}`)
      .join("；");
  }
  return textValue(value);
}

function entityLabel(value: Proposal): string {
  const name = textValue(value.name ?? value.display_name);
  const type = textValue(value.type);
  const role = textValue(value.role);
  return [name, type, role].filter((entry) => entry !== "—").join(" · ");
}

function ReviewField({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div className={`review-field${wide ? " review-field-wide" : ""}`}>
      <span>{label}</span>
      {children}
    </div>
  );
}

function CorrectionSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: readonly string[];
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </label>
  );
}

function RelevanceReview({ proposal }: { proposal: Proposal }) {
  const gate = objectValue(proposal.evidence_gate);
  return (
    <section className="review-content-panel">
      <ReviewField label="处理结论">
        <strong>{proposal.is_lol_relevant === true ? "保留" : "排除"}</strong>
      </ReviewField>
      <ReviewField label="产品范围">
        <strong>{textValue(proposal.product_scope)}</strong>
      </ReviewField>
      <ReviewField label="置信度">
        <strong>{scoreValue(proposal.confidence)}</strong>
      </ReviewField>
      <ReviewField label="依据" wide>
        <p>{textValue(proposal.reason)}</p>
      </ReviewField>
      <ReviewField label="可用证据" wide>
        <p>{textList(gate.evidence_sources)}</p>
      </ReviewField>
    </section>
  );
}

function TranslationReview({ proposal }: { proposal: Proposal }) {
  const media = objectList(proposal.translated_media_extractions);
  return (
    <section className="translation-review">
      <div className="translation-meta">
        <span>
          {textValue(proposal.source_language)} →{" "}
          {textValue(proposal.target_language)}
        </span>
        <b>{textValue(proposal.translation_status)}</b>
        <small>{textValue(proposal.translation_model)}</small>
      </div>
      <div className="translation-title">
        <span>中文标题</span>
        <strong>{textValue(proposal.translated_title)}</strong>
      </div>
      <div className="translation-columns">
        <article>
          <span>原文</span>
          <p>{textValue(proposal.normalized_text)}</p>
        </article>
        <article>
          <span>中文译文</span>
          <p>{textValue(proposal.translated_text)}</p>
        </article>
      </div>
      {media.length > 0 && (
        <details className="review-json-details">
          <summary>查看图片结构化译文（{media.length} 项）</summary>
          <pre>{JSON.stringify(media, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

function EventMentionsReview({ proposal }: { proposal: Proposal }) {
  const mentions = objectList(proposal.event_mentions);
  return (
    <ReviewField label="可聚合事件提及" wide>
      <div className="review-entity-list merged-entity-list">
        {mentions.length > 0 ? (
          mentions.map((mention, index) => {
            const temporal = objectValue(mention.temporal);
            const identities = objectList(mention.identity_entities)
              .map((entity) => textValue(entity.canonical_name ?? entity.name))
              .join("、");
            return (
              <code key={`${textValue(mention.subtopic)}-mention-${index}`}>
                <span>
                  {textValue(mention.topic)} / {textValue(mention.subtopic)} · {identities}
                </span>
                <b>
                  {textValue(mention.assertion)} · {textValue(mention.membership_role)} · {textValue(temporal.event_date)}
                </b>
              </code>
            );
          })
        ) : (
          <em>无可聚合事件提及</em>
        )}
      </div>
    </ReviewField>
  );
}

function CombinedFactClassificationReview({
  proposal,
}: {
  proposal: Proposal;
}) {
  const temporal = objectValue(proposal.temporal);
  const roles = objectList(proposal.entity_roles);
  const roleByName = new Map(
    roles.map((role) => [textValue(role.name), textValue(role.role)]),
  );
  const roleLabels: Record<string, string> = {
    core: "核心",
    context: "背景",
    affected: "受影响",
  };
  const entities: Proposal[] = objectList(proposal.entities).map((entity) => {
    const merged: Proposal = { ...entity };
    merged.role = roleByName.get(textValue(entity.name)) ?? "context";
    return merged;
  });
  return (
    <section className="fact-classify-review">
      <div className="review-content-panel">
        <ReviewField label="事实标题" wide>
          <strong>{textValue(proposal.title)}</strong>
        </ReviewField>
        <ReviewField label="事实摘要" wide>
          <p>{textValue(proposal.summary)}</p>
        </ReviewField>
      </div>
      <div className="review-content-panel review-four-columns">
        <ReviewField label="来源性质">
          <strong>{textValue(proposal.source_kind)}</strong>
        </ReviewField>
        <ReviewField label="信息阶段">
          <strong>{textValue(proposal.information_stage)}</strong>
        </ReviewField>
        <ReviewField label="内容形式">
          <strong>{textValue(proposal.content_form)}</strong>
        </ReviewField>
        <ReviewField label="主主题">
          <strong>{textValue(proposal.topic)}</strong>
        </ReviewField>
        <ReviewField label="子主题">
          <strong>{textValue(proposal.subtopic)}</strong>
        </ReviewField>
      </div>
      <div className="review-content-panel review-temporal-row">
        <ReviewField label="确定性">
          <strong>{textValue(temporal.certainty)}</strong>
        </ReviewField>
        <ReviewField label="周期内容">
          <strong>{temporal.is_recurring === true ? "是" : "否"}</strong>
        </ReviewField>
        <ReviewField label="周期">
          <strong>{textValue(temporal.recurrence_window)}</strong>
        </ReviewField>
        <ReviewField label="事件断言">
          <strong>{textValue(proposal.event_assertion)}</strong>
        </ReviewField>
      </div>
      <div className="review-content-panel">
        <ReviewField label="实体及其在消息中的作用" wide>
          <p className="review-field-help">
            实体是消息涉及的人、英雄、装备、赛事或版本；角色说明它是核心对象、背景信息，还是受影响对象。
          </p>
          <div className="review-entity-list merged-entity-list">
            {entities.map((entity, index) => {
              const role = textValue(entity.role);
              return (
                <code key={`${entityLabel(entity)}-${index}`}>
                  <span>
                    {textValue(entity.name)} · {textValue(entity.type)}
                  </span>
                  <b>{roleLabels[role] ?? role}</b>
                </code>
              );
            })}
            {entities.length === 0 && <em>未提取实体</em>}
          </div>
        </ReviewField>
      </div>
      <div className="review-content-panel">
        <EventMentionsReview proposal={proposal} />
      </div>
    </section>
  );
}

function ImportanceReview({ proposal }: { proposal: Proposal }) {
  const dimensions = objectValue(proposal.importance_dimensions);
  return (
    <section className="importance-review">
      <div className="importance-review-score">
        <span>最终重要性</span>
        <strong>{scoreValue(proposal.importance_score)}</strong>
        <small>{textValue(proposal.importance_policy_version)}</small>
      </div>
      <div className="importance-review-grid">
        {Object.entries(dimensionLabels).map(([key, label]) => {
          const dimension = objectValue(dimensions[key]);
          const value = dimension.score ?? dimension.value;
          return (
            <article key={key}>
              <header>
                <strong>{label}</strong>
                <b>
                  {typeof value === "number"
                    ? scoreValue(value, 4)
                    : textValue(value)}
                </b>
              </header>
              <p>{textValue(dimension.evidence)}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ClaimsReview({ proposal }: { proposal: Proposal }) {
  const attribution = objectValue(proposal.attribution);
  const compaction = objectValue(proposal._claim_compaction);
  const claims = objectList(proposal.fact_claims);
  const predicateLabels: Record<string, string> = {
    transfers_to: "转会至",
    considered_for: "被考虑加入",
    leaves: "离开",
    stays: "留队",
    retires: "退役",
    releases: "将发布",
    goes_live: "已上线",
    previews: "预告",
    delays: "延期",
    patches: "版本调整",
    buffs: "增强",
    nerfs: "削弱",
    reworks: "重做",
    adds_mode: "新增模式",
    wins: "战胜",
    loses: "负于",
    advances: "晋级",
    eliminated: "淘汰",
    rotates: "轮换",
    discounts: "折扣",
    gifts: "赠送",
  };
  return (
    <section className="claims-review">
      <div className="claims-review-intro">
        <strong>这些不是摘要的拆句</strong>
        <p>
          可追踪事实用于把同一事件中“预告、实装、延期、反驳”等变化串起来，并识别后续消息是否取代了较早说法。这里只应保留未来可能被确认、更新或推翻的事实。
        </p>
        <b>
          {typeof compaction.original_count === "number" &&
          compaction.original_count !== claims.length
            ? `${compaction.original_count} → ${claims.length} 条`
            : `${claims.length} 条待确认`}
        </b>
      </div>
      <div className="claims-attribution">
        <span>信源归因</span>
        <strong>{textValue(attribution.claimed_by)}</strong>
        <p>
          {textValue(attribution.stance)} ·{" "}
          {textValue(attribution.certainty)}
        </p>
      </div>
      <div className="claims-review-list">
        {claims.length > 0 ? (
          claims.map((claim, index) => (
            <article key={`${textValue(claim.predicate)}-${index}`}>
              <span>可追踪事实 {index + 1}</span>
              <strong>
                {structuredValue(claim.subject)} ·{" "}
                {predicateLabels[textValue(claim.predicate)] ??
                  textValue(claim.predicate)}
              </strong>
              <p>
                {Array.isArray(objectValue(claim.object).targets)
                  ? `涉及：${textList(objectValue(claim.object).targets)}`
                  : structuredValue(claim.object)}
              </p>
              <small>
                {textValue(claim.temporal_role)}
                {claim.supersedes_hint
                  ? ` · 更新提示：${textValue(claim.supersedes_hint)}`
                  : ""}
              </small>
            </article>
          ))
        ) : (
          <div className="admin-empty">没有生成事实断言。</div>
        )}
      </div>
    </section>
  );
}

function EventDecisionReview({ proposal }: { proposal: Proposal }) {
  const item = objectValue(proposal.item);
  const decision = objectValue(proposal.decision);
  const memberships = objectList(decision.memberships);
  const rejections = objectList(decision.candidate_rejections);
  const candidates = objectList(proposal.candidates);
  const candidateById = new Map(
    candidates.map((candidate) => [Number(candidate.event_id), candidate]),
  );
  return (
    <section className="event-decision-review">
      <div className="event-review-source">
        <span>本次待归属消息</span>
        <div>
          <strong>{textValue(item.title)}</strong>
          <p>{textValue(item.summary)}</p>
        </div>
        <b>{textValue(item.primary_topic)} · {textValue(item.subtopic)}</b>
      </div>
      <div className="event-review-decision-head">
        <div>
          <span>系统建议</span>
          <strong>
            {memberships.length > 0
              ? `归入 ${memberships.length} 个事件`
              : "不形成事件"}
          </strong>
        </div>
        <p>
          {memberships.length > 0
            ? "核对目标事件、时间线文案，以及这条消息在事件中的作用。"
            : "请结合候选事件和排除理由判断是否确实无需形成事件。"}
        </p>
      </div>
      <div className="event-review-memberships">
        {memberships.length > 0 ? (
          memberships.map((membership, index) => {
            const target = textValue(membership.target);
            const existingId = target.startsWith("existing:")
              ? Number(target.split(":")[1])
              : null;
            const candidate = existingId
              ? candidateById.get(existingId)
              : undefined;
            return (
              <article key={`${target}-${index}`}>
                <header>
                  <span>{existingId ? "归入已有事件" : "创建新事件"}</span>
                  <b>{existingId ? `Event #${existingId}` : "NEW EVENT"}</b>
                </header>
                <h3>
                  {candidate
                    ? textValue(candidate.title)
                    : textValue(membership.timeline_note)}
                </h3>
                {candidate && <p>{textValue(candidate.summary)}</p>}
                <div className="event-review-timeline-note">
                  <span>写入时间线</span>
                  <strong>{textValue(membership.timeline_note)}</strong>
                </div>
                <dl>
                  <div><dt>事件事实</dt><dd>{textValue(membership.event_kind)}</dd></div>
                  <div><dt>聚合策略</dt><dd>{textValue(membership.aggregation_strategy)}</dd></div>
                  <div><dt>消息角色</dt><dd>{textValue(membership.membership_role)}</dd></div>
                  <div><dt>身份解析</dt><dd>{textValue(membership.identity_resolution)}</dd></div>
                  <div><dt>证据立场</dt><dd>{textValue(membership.evidence_stance)}</dd></div>
                  <div><dt>更新类型</dt><dd>{textValue(membership.update_kind)}</dd></div>
                  <div><dt>事件状态</dt><dd>{textValue(membership.lifecycle_status)}</dd></div>
                </dl>
                {Boolean(membership.identity_rationale) && (
                  <p>{textValue(membership.identity_rationale)}</p>
                )}
                <code>{textValue(membership.aggregation_key)}</code>
              </article>
            );
          })
        ) : (
          <div className="event-review-no-membership">
            <strong>建议作为独立消息发布</strong>
            <p>不会创建事件，也不会加入已有事件。</p>
          </div>
        )}
      </div>
      <div className="event-review-candidates">
        <header>
          <div>
            <span>检索到的候选事件</span>
            <strong>{candidates.length} 个</strong>
          </div>
          <small>核对是否漏掉应归入的已有事件</small>
        </header>
        {candidates.length > 0 ? (
          candidates.map((candidate) => {
            const rejection = rejections.find(
              (entry) => Number(entry.event_id) === Number(candidate.event_id),
            );
            const selected = memberships.some(
              (membership) =>
                textValue(membership.target) ===
                `existing:${candidate.event_id}`,
            );
            return (
              <article
                className={selected ? "selected" : "rejected"}
                key={String(candidate.event_id)}
              >
                <div>
                  <span>
                    Event #{textValue(candidate.event_id)} ·{" "}
                    {textValue(candidate.event_kind)}
                  </span>
                  <strong>{textValue(candidate.title)}</strong>
                  <p>{textValue(candidate.summary)}</p>
                </div>
                <div className="event-candidate-match">
                  <b>{selected ? "已选择" : "未选择"}</b>
                  <span>
                    匹配 {textValue(candidate.match_level)} ·{" "}
                    {typeof candidate.score === "number"
                      ? `${candidate.score.toFixed(1)} 分`
                      : "—"}
                  </span>
                  <small>
                    {rejection
                      ? textValue(rejection.reason)
                      : textList(candidate.reasons)}
                  </small>
                </div>
              </article>
            );
          })
        ) : (
          <div className="admin-empty">没有检索到已有事件候选。</div>
        )}
      </div>
    </section>
  );
}

function ProposalPreview({
  stage,
  proposal,
}: {
  stage: string;
  proposal: Proposal;
}) {
  if (stage === "relevance") return <RelevanceReview proposal={proposal} />;
  if (stage === "translation") return <TranslationReview proposal={proposal} />;
  if (stage === "fact_classify") {
    return <CombinedFactClassificationReview proposal={proposal} />;
  }
  if (stage === "importance") {
    return <ImportanceReview proposal={proposal} />;
  }
  if (stage === "claim_gen") return <ClaimsReview proposal={proposal} />;
  return (
    <div className="review-content-panel">
      <ReviewField label="审核内容" wide>
        <p>该阶段暂无专用视图，请展开审计详情查看。</p>
      </ReviewField>
    </div>
  );
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
  const [glossaryUpdates, setGlossaryUpdates] = useState([
    {
      source_term: "",
      preferred_translation: "",
      forbidden_translations: "",
      scope: "lol",
      notes: "",
    },
  ]);
  const stage = "stage" in review ? review.stage : "event_decision";
  const [sourceKind, setSourceKind] = useState(
    typeof review.proposal.source_kind === "string"
      ? review.proposal.source_kind
      : "",
  );
  const [productScope, setProductScope] = useState(
    typeof review.proposal.product_scope === "string"
      ? review.proposal.product_scope
      : "uncertain",
  );
  const [isRelevant, setIsRelevant] = useState(
    review.proposal.is_lol_relevant === true,
  );
  const [informationStage, setInformationStage] = useState(
    typeof review.proposal.information_stage === "string"
      ? review.proposal.information_stage
      : "",
  );
  const [contentForm, setContentForm] = useState(
    typeof review.proposal.content_form === "string"
      ? review.proposal.content_form
      : "",
  );
  const [eventAssertion, setEventAssertion] = useState(
    typeof review.proposal.event_assertion === "string"
      ? review.proposal.event_assertion
      : "asserted",
  );
  const [eventMentionsDraft, setEventMentionsDraft] = useState(
    JSON.stringify(objectList(review.proposal.event_mentions), null, 2),
  );
  const [primaryTopic, setPrimaryTopic] = useState(
    typeof review.proposal.primary_topic === "string"
      ? review.proposal.primary_topic
      : typeof review.proposal.topic === "string"
        ? review.proposal.topic
        : "",
  );
  const [subtopic, setSubtopic] = useState(
    typeof review.proposal.subtopic === "string"
      ? review.proposal.subtopic
      : "",
  );
  const [importance, setImportance] = useState(
    typeof review.proposal.importance_score === "number"
      ? String(Math.round(review.proposal.importance_score * 100))
      : "",
  );
  const [eventDraft, setEventDraft] = useState(
    JSON.stringify(review.proposal.decision ?? review.proposal, null, 2),
  );
  const base =
    kind === "event" ? "/event-workflows/reviews" : "/workflows/reviews";
  const isOcrStage = kind === "message" && stage === "image_ocr";
  const canCorrect =
    kind === "event" ||
    ["relevance", "fact_classify", "importance"].includes(stage);

  const act = async (action: "approve" | "reject") => {
    setBusy(action);
    setError(null);
    try {
      const body =
        action === "approve"
          ? { note: null }
          : kind === "event"
            ? { reason: note || "管理台拒绝该建议" }
            : isOcrStage
              ? { feedback_type: "ocr_error", reason: null }
            : {
                feedback_type:
                  stage === "translation"
                    ? "translation_correction"
                    : "analysis_correction",
                reason:
                  note ||
                  (stage === "translation" ? null : "管理台退回该审核结果"),
                corrected_values: {},
                glossary_updates:
                  stage === "translation"
                    ? glossaryUpdates
                        .filter(
                          (entry) =>
                            entry.source_term.trim() &&
                            entry.preferred_translation.trim(),
                        )
                        .map((entry) => ({
                          source_term: entry.source_term.trim(),
                          preferred_translation:
                            entry.preferred_translation.trim(),
                          forbidden_translations:
                            entry.forbidden_translations
                              .split(/[,，\n]/)
                              .map((value) => value.trim())
                              .filter(Boolean),
                          scope: entry.scope.trim() || "lol",
                          notes: entry.notes.trim() || null,
                        }))
                    : [],
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
              decision_draft: JSON.parse(eventDraft) as Proposal,
              note: null,
            }
          : {
              product_scope: stage === "relevance" ? productScope : null,
              is_lol_relevant: stage === "relevance" ? isRelevant : null,
              source_kind: sourceKind || null,
              information_stage: informationStage || null,
              content_form: contentForm || null,
              event_assertion:
                stage === "fact_classify"
                  ? eventAssertion
                  : null,
              event_mentions:
                stage === "fact_classify"
                  ? (JSON.parse(eventMentionsDraft) as Proposal[])
                  : null,
              primary_topic: primaryTopic || null,
              subtopic: subtopic || null,
              importance_score:
                stage === "importance" && importance !== ""
                  ? Number(importance) / 100
                  : null,
              note: null,
            };
      await adminApi(`${base}/${review.id}/correct-and-approve`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      onResolved();
    } catch (value) {
      setError(
        value instanceof Error ? value.message : "修正提交失败，请检查字段",
      );
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="admin-review-card stage-review-card">
      <header>
        <div>
          <span>{kind === "event" ? "事件归属" : "消息处理"}</span>
          <strong>审核 #{review.id}</strong>
        </div>
        <b>{stageLabels[stage] ?? stage}</b>
      </header>

      {kind === "event" ? (
        <EventDecisionReview proposal={review.proposal} />
      ) : (
        <ProposalPreview stage={stage} proposal={review.proposal} />
      )}

      <details className="review-json-details">
        <summary>查看完整审核草稿 JSON</summary>
        <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
      </details>

      {editing && canCorrect && kind === "event" && (
        <label className="admin-review-note">
          修改事件决策 JSON
          <textarea
            value={eventDraft}
            onChange={(event) => setEventDraft(event.target.value)}
          />
        </label>
      )}
      {editing && canCorrect && kind === "message" && (
        <div className="admin-correction-grid">
          {stage === "relevance" && (
            <>
              <CorrectionSelect label="产品范围" value={productScope} options={correctionOptions.productScope} onChange={(value) => {
                setProductScope(value);
                setIsRelevant(["lol_pc", "lol_esports", "tft", "lol_universe", "riot_corporate", "lol_merch_music"].includes(value));
              }} />
              <label>
                保留为英雄联盟资讯
                <input type="checkbox" checked={isRelevant} onChange={(event) => setIsRelevant(event.target.checked)} />
              </label>
            </>
          )}
          {stage === "fact_classify" && (
            <>
              <CorrectionSelect label="来源性质" value={sourceKind} options={correctionOptions.sourceKind} onChange={setSourceKind} />
              <CorrectionSelect label="信息阶段" value={informationStage} options={correctionOptions.informationStage} onChange={setInformationStage} />
              <CorrectionSelect label="内容形式" value={contentForm} options={correctionOptions.contentForm} onChange={setContentForm} />
              <CorrectionSelect label="事件断言" value={eventAssertion} options={correctionOptions.eventAssertion} onChange={setEventAssertion} />
              <CorrectionSelect label="主主题" value={primaryTopic} options={correctionOptions.primaryTopic} onChange={setPrimaryTopic} />
              <CorrectionSelect label="子主题" value={subtopic} options={correctionOptions.subtopic} onChange={setSubtopic} />
            </>
          )}
          {stage === "importance" && (
            <label>
              重要性 0–100
              <input
                type="number"
                min="0"
                max="100"
                step="1"
                value={importance}
                onChange={(event) => setImportance(event.target.value)}
              />
            </label>
          )}
          {stage === "fact_classify" && (
            <label className="admin-review-note">
              修改事件提及 JSON
              <textarea
                value={eventMentionsDraft}
                onChange={(event) => setEventMentionsDraft(event.target.value)}
              />
            </label>
          )}
        </div>
      )}
      {editing && kind === "message" && stage === "translation" && (
        <div className="translation-glossary-editor">
          <header>
            <div>
              <strong>术语修正</strong>
              <span>填写原词和正确译法，退回翻译后加入术语表。</span>
            </div>
            <button
              type="button"
              onClick={() =>
                setGlossaryUpdates((current) => [
                  ...current,
                  {
                    source_term: "",
                    preferred_translation: "",
                    forbidden_translations: "",
                    scope: "lol",
                    notes: "",
                  },
                ])
              }
            >
              添加一项
            </button>
          </header>
          {glossaryUpdates.map((entry, index) => (
            <div className="translation-glossary-row" key={index}>
              <label>
                原文术语
                <input
                  value={entry.source_term}
                  onChange={(event) =>
                    setGlossaryUpdates((current) =>
                      current.map((value, valueIndex) =>
                        valueIndex === index
                          ? { ...value, source_term: event.target.value }
                          : value,
                      ),
                    )
                  }
                />
              </label>
              <label>
                正确译法
                <input
                  value={entry.preferred_translation}
                  onChange={(event) =>
                    setGlossaryUpdates((current) =>
                      current.map((value, valueIndex) =>
                        valueIndex === index
                          ? {
                              ...value,
                              preferred_translation: event.target.value,
                            }
                          : value,
                      ),
                    )
                  }
                />
              </label>
              <label>
                禁用译法（逗号分隔）
                <input
                  value={entry.forbidden_translations}
                  onChange={(event) =>
                    setGlossaryUpdates((current) =>
                      current.map((value, valueIndex) =>
                        valueIndex === index
                          ? {
                              ...value,
                              forbidden_translations: event.target.value,
                            }
                          : value,
                      ),
                    )
                  }
                />
              </label>
              <button
                type="button"
                onClick={() =>
                  setGlossaryUpdates((current) =>
                    current.filter((_, valueIndex) => valueIndex !== index),
                  )
                }
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}
      {editing && !isOcrStage && (
        <label className="admin-review-note">
          退回意见
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={
              stage === "translation"
                ? "指出译文或术语问题"
                : "指出判断错误和正确处理方式"
            }
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
              ? "批准事件决策"
              : stage === "claim_gen"
                ? "批准并发布消息"
                : "批准并进入下一阶段"}
        </button>
        {!isOcrStage && (
          <button type="button" onClick={() => setEditing((value) => !value)}>
            {editing ? "收起操作" : canCorrect ? "修正或退回" : "填写退回意见"}
          </button>
        )}
        {editing && canCorrect && (
          <button
            type="button"
            onClick={() => void correctAndApprove()}
            disabled={busy !== null}
          >
            {busy === "correct" ? "提交中…" : "提交修正并批准"}
          </button>
        )}
        {(editing || isOcrStage) && (
          <button
            className="danger"
            type="button"
            onClick={() => void act("reject")}
            disabled={busy !== null || (!isOcrStage && !note.trim())}
          >
            {busy === "reject"
              ? "退回中…"
              : isOcrStage
                ? "退回重新处理"
                : "退回"}
          </button>
        )}
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
    </article>
  );
}
