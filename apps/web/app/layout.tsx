import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LeagueNews · 峡谷资讯纪事", template: "%s · LeagueNews" },
  description: "围绕英雄联盟消息、事件与日报构建的可核验资讯产品。",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `try { document.documentElement.dataset.theme = localStorage.getItem("leaguenews-theme") === "dark" ? "dark" : "light"; } catch { document.documentElement.dataset.theme = "light"; }`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
