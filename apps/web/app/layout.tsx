import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LoL Daily Intel",
  description: "英雄联盟每日多信源 AI 情报",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

