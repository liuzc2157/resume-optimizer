"use client";

import ReactMarkdown from "react-markdown";

// 轻量样式的 Markdown 渲染器：用于展示 Agent 输出的分析/改写结果
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed text-slate-700 [&_h1]:mb-2 [&_h1]:text-base [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-base [&_h2]:font-semibold [&_h3]:mb-2 [&_h3]:font-semibold [&_li]:mb-1 [&_ol]:mb-3 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:mb-2 [&_p]:last:mb-0 [&_strong]:font-semibold [&_strong]:text-slate-900 [&_ul]:mb-3 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown>{children}</ReactMarkdown>
    </div>
  );
}
