"use client";

import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/lib/api";

type Rule = {
  id: number;
  knowledge_type: string;
  scope: string;
  rule_text: string;
  lifecycle_status: string;
  is_active: boolean;
  version: number;
  updated_at: string;
};
type Term = {
  id: number;
  source_term: string;
  preferred_translation: string;
  scope: string;
  notes: string | null;
  is_active: boolean;
  version: number;
};

export default function KnowledgePage() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [terms, setTerms] = useState<Term[]>([]);
  const [tab, setTab] = useState<"rules" | "terms">("rules");
  const [ruleText, setRuleText] = useState("");
  const [term, setTerm] = useState("");
  const [translation, setTranslation] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    setError(null);
    try {
      const [ruleRows, termRows] = await Promise.all([
        adminApi<Rule[]>("/knowledge/rules"),
        adminApi<Term[]>("/knowledge/glossary"),
      ]);
      setRules(ruleRows);
      setTerms(termRows);
    } catch (value) {
      setError(value instanceof Error ? value.message : "知识库加载失败");
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const act = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };
  const addRule = () =>
    act("add-rule", () =>
      adminApi("/knowledge/rules", {
        method: "POST",
        body: JSON.stringify({
          knowledge_type: "analysis",
          scope: "global",
          rule_text: ruleText,
          correction_data: {},
          lifecycle_status: "draft",
          is_active: false,
        }),
      }).then(() => setRuleText("")),
    );
  const addTerm = () =>
    act("add-term", () =>
      adminApi("/knowledge/glossary", {
        method: "POST",
        body: JSON.stringify({
          source_term: term,
          preferred_translation: translation,
          forbidden_translations: [],
          scope: "lol",
          notes: null,
          is_active: true,
        }),
      }).then(() => {
        setTerm("");
        setTranslation("");
      }),
    );
  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">GOVERNANCE</span>
          <h1>知识库</h1>
          <p>
            维护分析规则与翻译术语；历史相关性规则仅供追溯，不再参与新判断。
          </p>
        </div>
        <button
          className="admin-primary-button"
          disabled={busy !== null}
          onClick={() =>
            void act("organize", () =>
              adminApi("/knowledge/rules/organize", {
                method: "POST",
                body: "{}",
              }),
            )
          }
        >
          {busy === "organize" ? "整理中…" : "AI 整理规则"}
        </button>
      </header>
      {error && (
        <div className="admin-error-state">
          <span>{error}</span>
          <button onClick={() => void load()}>重试</button>
        </div>
      )}
      <div className="admin-queue-tabs">
        <button
          className={tab === "rules" ? "active" : ""}
          onClick={() => setTab("rules")}
        >
          知识规则 <b>{rules.length}</b>
        </button>
        <button
          className={tab === "terms" ? "active" : ""}
          onClick={() => setTab("terms")}
        >
          术语表 <b>{terms.length}</b>
        </button>
      </div>
      {tab === "rules" ? (
        <>
          <form
            className="admin-knowledge-create"
            onSubmit={(event) => {
              event.preventDefault();
              void addRule();
            }}
          >
            <label>
              新增分析规则
              <textarea
                required
                value={ruleText}
                onChange={(event) => setRuleText(event.target.value)}
                placeholder="描述稳定、可复用的判断规则"
              />
            </label>
            <button disabled={busy !== null}>创建 draft</button>
          </form>
          <div className="admin-knowledge-list">
            {rules.map((rule) => (
              <article
                key={rule.id}
                className={
                  !rule.is_active || rule.knowledge_type === "relevance"
                    ? "inactive"
                    : ""
                }
              >
                <header>
                  <span className="admin-badge">{rule.knowledge_type}</span>
                  <span>{rule.scope}</span>
                  <b>
                    {rule.lifecycle_status} · v{rule.version}
                  </b>
                </header>
                <p>{rule.rule_text}</p>
                {rule.knowledge_type === "relevance" ? (
                  <div className="admin-inline-actions">
                    <span className="admin-muted">历史留档 · 只读</span>
                  </div>
                ) : (
                  <div className="admin-inline-actions">
                    <button
                      disabled={
                        busy !== null || rule.lifecycle_status !== "draft"
                      }
                      onClick={() =>
                        void act(`eval-${rule.id}`, () =>
                          adminApi(`/knowledge/rules/${rule.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({
                              lifecycle_status: "evaluated",
                              evaluation_summary: {
                                source: "admin",
                                note: "人工确认",
                              },
                            }),
                          }),
                        )
                      }
                    >
                      标记已评估
                    </button>
                    <button
                      disabled={
                        busy !== null || rule.lifecycle_status !== "evaluated"
                      }
                      onClick={() =>
                        void act(`activate-${rule.id}`, () =>
                          adminApi(`/knowledge/rules/${rule.id}`, {
                            method: "PATCH",
                            body: JSON.stringify({
                              lifecycle_status: "active",
                            }),
                          }),
                        )
                      }
                    >
                      激活
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        </>
      ) : (
        <>
          <form
            className="admin-term-create"
            onSubmit={(event) => {
              event.preventDefault();
              void addTerm();
            }}
          >
            <label>
              源术语
              <input
                required
                value={term}
                onChange={(event) => setTerm(event.target.value)}
              />
            </label>
            <label>
              首选译法
              <input
                required
                value={translation}
                onChange={(event) => setTranslation(event.target.value)}
              />
            </label>
            <button disabled={busy !== null}>新增术语</button>
          </form>
          <div className="admin-table-scroll">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>源术语</th>
                  <th>首选译法</th>
                  <th>范围</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {terms.map((entry) => (
                  <tr key={entry.id}>
                    <td>{entry.source_term}</td>
                    <td>
                      <strong>{entry.preferred_translation}</strong>
                    </td>
                    <td>{entry.scope}</td>
                    <td>{entry.is_active ? "active" : "inactive"}</td>
                    <td>
                      <button
                        className="admin-table-button"
                        disabled={busy !== null}
                        onClick={() =>
                          void act(`term-${entry.id}`, () =>
                            adminApi(`/knowledge/glossary/${entry.id}`, {
                              method: "PATCH",
                              body: JSON.stringify({
                                is_active: !entry.is_active,
                              }),
                            }),
                          )
                        }
                      >
                        {entry.is_active ? "停用" : "启用"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
