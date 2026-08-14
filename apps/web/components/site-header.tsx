"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const navigation = [
  { href: "/", label: "首页", match: (pathname: string) => pathname === "/" },
  { href: "/messages", label: "消息", match: (pathname: string) => pathname.startsWith("/messages") },
  { href: "/events", label: "事件", match: (pathname: string) => pathname.startsWith("/events") },
  { href: "/daily", label: "日报", match: (pathname: string) => pathname.startsWith("/daily") },
];

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <header className="public-header">
      <div className="public-header-inner">
        <Link className="public-brand" href="/" aria-label="LeagueNews 首页" onClick={() => setOpen(false)}>
          <span className="public-brand-mark" aria-hidden="true"><i />LN</span>
          <span className="public-brand-name">LeagueNews<small>峡谷资讯纪事</small></span>
        </Link>
        <button
          className="public-menu-button"
          type="button"
          aria-label={open ? "关闭导航" : "打开导航"}
          aria-expanded={open}
          aria-controls="public-navigation"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <X size={20} /> : <Menu size={20} />}
        </button>
        <nav id="public-navigation" className={`public-navigation ${open ? "open" : ""}`} aria-label="主要导航">
          {navigation.map((item) => {
            const active = item.match(pathname);
            return (
              <Link
                className={active ? "active" : ""}
                href={item.href}
                aria-current={active ? "page" : undefined}
                onClick={() => setOpen(false)}
                key={item.href}
              >
                {item.label}
              </Link>
            );
          })}
          <Link className="public-admin-link" href="/admin" onClick={() => setOpen(false)}>管理台</Link>
        </nav>
      </div>
    </header>
  );
}
