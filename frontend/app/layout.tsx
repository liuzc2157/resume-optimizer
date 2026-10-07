import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI 简历优化 Agent",
  description: "基于 LangGraph 编排的简历匹配分析与优化 Agent",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="relative min-h-screen bg-[#f7f6ff] text-slate-900 antialiased">
        {/* 背景装饰：彩色光斑 + 细网格，增强现代设计感 */}
        <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
          <div className="absolute -left-28 -top-28 h-96 w-96 rounded-full bg-violet-300/35 blur-3xl" />
          <div className="absolute -right-32 top-1/4 h-[28rem] w-[28rem] rounded-full bg-indigo-300/25 blur-3xl" />
          <div className="absolute bottom-0 left-1/4 h-80 w-80 rounded-full bg-rose-200/30 blur-3xl" />
          <div className="absolute bottom-1/4 right-1/4 h-64 w-64 rounded-full bg-sky-200/20 blur-3xl" />
          <div className="absolute inset-0 bg-[linear-gradient(to_right,rgba(99,102,241,0.05)_1px,transparent_1px),linear-gradient(to_bottom,rgba(99,102,241,0.05)_1px,transparent_1px)] bg-[size:36px_36px]" />
        </div>
        {children}
      </body>
    </html>
  );
}
