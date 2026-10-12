import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "简遇 Briefly · AI 简历优化",
  description: "ATS 多维评分、AI 分析改写、岗位智能匹配的一站式简历优化平台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="relative min-h-screen bg-[#f7f6ff] text-slate-900 antialiased">
        {/* 背景装饰：彩色光斑 + 点阵网格，增强现代设计感 */}
        <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          <div className="absolute -left-28 -top-28 h-96 w-96 animate-blob rounded-full bg-violet-300/35 blur-3xl" />
          <div
            className="absolute -right-32 top-1/4 h-[28rem] w-[28rem] animate-blob rounded-full bg-indigo-300/25 blur-3xl"
            style={{ animationDelay: "-6s" }}
          />
          <div
            className="absolute bottom-0 left-1/4 h-80 w-80 animate-blob rounded-full bg-rose-200/30 blur-3xl"
            style={{ animationDelay: "-3s" }}
          />
          <div
            className="absolute bottom-1/4 right-1/4 h-64 w-64 animate-blob rounded-full bg-sky-200/20 blur-3xl"
            style={{ animationDelay: "-10s" }}
          />
          <div className="absolute inset-0 bg-dot-grid" />
        </div>
        {children}
      </body>
    </html>
  );
}