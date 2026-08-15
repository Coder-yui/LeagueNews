"use client";

import { Menu, Moon, Sun, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const navigation = [
  { href: "/", label: "首页", match: (pathname: string) => pathname === "/" },
  { href: "/messages", label: "消息", match: (pathname: string) => pathname.startsWith("/messages") },
  { href: "/events", label: "事件", match: (pathname: string) => pathname.startsWith("/events") },
  { href: "/daily", label: "日报", match: (pathname: string) => pathname.startsWith("/daily") },
];

export function SiteHeader() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");

  useEffect(() => {
    const initialTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    setTheme(initialTheme);
  }, []);

  function toggleTheme() {
    const currentTheme = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    const nextTheme = currentTheme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("leaguenews-theme", nextTheme);
    setTheme(nextTheme);
  }

  return (
    <header className="public-header">
      <div className="public-header-inner">
        <div className="public-brand-cluster">
          <Link className="public-brand" href="/" aria-label="LeagueNews 首页" onClick={() => setOpen(false)}>
            <span className="public-brand-mark" aria-hidden="true"><i />LN</span>
            <span className="public-brand-name">LeagueNews<small>峡谷资讯纪事</small></span>
          </Link>
          <button
            className="public-theme-toggle"
            type="button"
            aria-label={theme === "light" ? "切换深色主题" : "切换浅色主题"}
            title={theme === "light" ? "深色主题" : "浅色主题"}
            aria-pressed={theme === "dark"}
            onClick={toggleTheme}
          >
            <Sun className="public-theme-icon public-theme-icon-sun" size={16} strokeWidth={1.8} />
            <Moon className="public-theme-icon public-theme-icon-moon" size={16} strokeWidth={1.8} />
          </button>
        </div>
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
        </nav>
      </div>
    </header>
  );
}
