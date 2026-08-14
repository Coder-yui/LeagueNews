import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="public-footer">
      <div>
        <strong>LeagueNews</strong>
        <span>从消息到事件，保留可核验的资讯脉络。</span>
      </div>
      <nav aria-label="页脚导航">
        <Link href="/messages">消息归档</Link>
        <Link href="/events">事件追踪</Link>
        <Link href="/daily">每日日报</Link>
      </nav>
      <small>LeagueNews 为独立资讯项目，与 Riot Games 无隶属关系。</small>
    </footer>
  );
}
