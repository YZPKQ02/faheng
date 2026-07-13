import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "法衡 · 劳动争议助手",
  description: "可追溯的多智能体劳动争议咨询与诉讼沙盘",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

