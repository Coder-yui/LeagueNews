"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, ArrowLeft, BookOpen, Database, FileText, GitBranch, Menu, ScanText, Settings, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

const groups = [
  { label: "", items: [{ href: "/admin/pipeline", label: "流水线监控", icon: Activity }] },
  { label: "内容", items: [{ href: "/admin/messages", label: "消息管理", icon: FileText }, { href: "/admin/events", label: "事件管理", icon: GitBranch }] },
  { label: "运营", items: [{ href: "/admin/reviews", label: "审核中心", icon: ShieldCheck, review: true }, { href: "/admin/collection", label: "数据采集", icon: Database }] },
  { label: "系统", items: [{ href: "/admin/system", label: "系统运维", icon: Settings }, { href: "/admin/system/ocr", label: "OCR 测试台", icon: ScanText }, { href: "/admin/system/knowledge", label: "知识库", icon: BookOpen }] },
];

export function SideNav({ reviewCount, failedJobs }: { reviewCount: number; failedJobs: number }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <>
      <div className="admin-mobile-bar">
        <strong>LeagueNews 管理</strong>
        <button type="button" onClick={() => setOpen((value) => !value)} aria-label={open ? "关闭导航" : "打开导航"}>{open ? <X /> : <Menu />}</button>
      </div>
      <aside className={`admin-sidebar ${open ? "open" : ""}`}>
        <Link className="admin-sidebar-brand" href="/admin/pipeline" onClick={() => setOpen(false)}>
          <span>LN</span><strong>LeagueNews<small>管理控制台</small></strong>
        </Link>
        <nav className="admin-side-nav" aria-label="管理台导航">
          <div className="admin-nav-group">
            <Link href="/" className="admin-feed-link" onClick={() => setOpen(false)}><ArrowLeft size={16} /><span>返回消息流</span></Link>
          </div>
          {groups.map((group) => (
            <div className="admin-nav-group" key={group.label || "primary"}>
              {group.label && <p>{group.label}</p>}
              {group.items.map((item) => {
                const active = pathname === item.href || (item.href !== "/admin/system" && pathname.startsWith(`${item.href}/`));
                const Icon = item.icon;
                return <Link key={item.href} href={item.href} className={active ? "active" : ""} onClick={() => setOpen(false)}><Icon size={16} /><span>{item.label}</span>{item.review && reviewCount > 0 && <b>{reviewCount}</b>}</Link>;
              })}
            </div>
          ))}
        </nav>
        <div className={`admin-health ${failedJobs > 0 ? "failed" : "healthy"}`}><span aria-hidden="true" />{failedJobs > 0 ? `${failedJobs} 个失败任务` : "系统健康"}</div>
      </aside>
      {open && <button className="admin-nav-scrim" aria-label="关闭导航" onClick={() => setOpen(false)} />}
    </>
  );
}
