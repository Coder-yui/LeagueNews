"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/lib/api";
import type { DailyReport, DailyReportSummary } from "@/lib/types";


function shanghaiDate(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}


export default function AdminReportsPage() {
  const [reports, setReports] = useState<DailyReportSummary[]>([]);
  const [reportDate, setReportDate] = useState(shanghaiDate);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setReports(await adminApi<DailyReportSummary[]>("/reports/daily"));
      setError(null);
    } catch (value) {
      setError(value instanceof Error ? value.message : "日报列表加载失败");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const generate = async (date: string) => {
    setBusy(`generate:${date}`);
    setError(null);
    try {
      await adminApi<DailyReport>(`/reports/daily/${date}/generate`, {
        method: "POST",
        body: "{}",
      });
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "日报生成失败");
    } finally {
      setBusy(null);
    }
  };

  const withdraw = async (date: string) => {
    if (!window.confirm(`确认退回 ${date} 的公开日报？日报记录会保留，但公开页面将不再展示。`)) return;
    setBusy(`withdraw:${date}`);
    setError(null);
    try {
      await adminApi<DailyReportSummary>(`/reports/daily/${date}/withdraw`, {
        method: "POST",
        body: "{}",
      });
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "日报退回失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">EDITORIAL OUTPUT</span>
          <h1>日报管理</h1>
          <p>日报退回只撤下公开投影；人工重新生成会按当前消息和事件状态覆盖同一天日报。</p>
        </div>
      </header>

      {error && <div className="admin-error-state"><span>{error}</span><button onClick={() => void load()}>重试</button></div>}

      <section className="admin-panel no-margin">
        <div className="admin-section-title"><h2>手工生成</h2><span>北京时间自然日</span></div>
        <div className="admin-inline-actions">
          <input type="date" value={reportDate} onChange={(event) => setReportDate(event.target.value)} />
          <button type="button" disabled={!reportDate || busy !== null} onClick={() => void generate(reportDate)}>
            {busy === `generate:${reportDate}` ? "生成中…" : "生成 / 重新生成"}
          </button>
        </div>
      </section>

      <section className="admin-panel">
        <div className="admin-section-title"><h2>日报记录</h2><span>{reports.length} 天</span></div>
        {reports.length === 0 ? <div className="admin-empty">还没有日报记录。</div> : (
          <div className="admin-table-scroll">
            <table className="admin-table">
              <thead><tr><th>日期</th><th>状态</th><th>条目</th><th>栏目</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>{reports.map((report) => (
                <tr key={report.id}>
                  <td className="admin-number">{report.report_date}</td>
                  <td><span className={`admin-badge ${report.status === "published" ? "success" : "danger"}`}>{report.status}</span></td>
                  <td>{report.item_count}</td>
                  <td>LoL {report.section_counts.lolpc} · 电竞 {report.section_counts.esports} · TFT {report.section_counts.tft} · 其他 {report.section_counts.other}</td>
                  <td>{new Date(report.updated_at).toLocaleString("zh-CN")}</td>
                  <td><div className="admin-row-actions">
                    {report.status === "published" && <Link href={`/daily?date=${report.report_date}`} target="_blank">查看</Link>}
                    <button type="button" disabled={busy !== null} onClick={() => { if (window.confirm(`确认按当前消息与事件状态重新生成 ${report.report_date} 的日报？`)) void generate(report.report_date); }}>重新生成</button>
                    {report.status === "published" && <button className="danger" type="button" disabled={busy !== null} onClick={() => void withdraw(report.report_date)}>退回</button>}
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
