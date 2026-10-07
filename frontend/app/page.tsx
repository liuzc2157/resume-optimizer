"use client";

import { useCallback, useEffect, useState } from "react";
import Chat from "./chat";
import QuickOptimize, { RestoreRequest } from "./quick-optimize";
import Match from "./match";
import {
  HISTORY_KEY,
  HistoryItem,
  formatDate,
  scoreColor,
} from "./history-utils";

type Tab = "quick" | "chat" | "match";

// 每个模式的一句话说明，直接显示在切换按钮下方，避免用户不知道该用哪个
const TAB_DESC: Record<Tab, string> = {
  quick: "已有明确 JD：粘贴简历 + 岗位描述，一键得出 ATS 多维评分、AI 分析与改写建议。",
  chat: "边聊边改：多轮对话追问细节，适合需要反复调整措辞、逐句打磨的场景。",
  match:
    "还没有明确 JD：先从岗位库按「岗位类别 + 经验等级」找出最匹配的岗位，再针对性优化；这里也能看缓存命中情况。",
};

export default function Home() {
  const [tab, setTab] = useState<Tab>("quick");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [restoreRequest, setRestoreRequest] = useState<RestoreRequest | null>(null);

  // 首次挂载时从 localStorage 恢复历史记录
  useEffect(() => {
    try {
      const raw = localStorage.getItem(HISTORY_KEY);
      if (raw) {
        setHistory(JSON.parse(raw) as HistoryItem[]);
      }
    } catch (error) {
      console.error(error);
    }
  }, []);

  const persistHistory = useCallback((items: HistoryItem[]) => {
    setHistory(items);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(items));
    } catch (error) {
      console.error(error);
    }
  }, []);

  const clearHistory = () => {
    if (confirm("确定清空全部历史记录吗？")) {
      persistHistory([]);
    }
  };

  const restoreItem = (item: HistoryItem) => {
    setRestoreRequest({ item, seq: Date.now() });
    setPanelOpen(false);
    setTab("quick");
  };

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <header className="relative text-center">
        {/* 右上角：历史记录入口，仅在「快速优化」模式下显示 */}
        {tab === "quick" && (
        <div className="absolute right-0 top-0">
          <button
            onClick={() => setPanelOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-full border border-violet-200 bg-white/70 px-4 py-1.5 text-xs font-medium text-slate-600 shadow-sm backdrop-blur transition hover:border-violet-400 hover:text-violet-600"
          >
            <span>历史记录</span>
            {history.length > 0 && (
              <span className="rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
                {history.length}
              </span>
            )}
            <span className="text-[10px] text-slate-400">
              {panelOpen ? "▲" : "▼"}
            </span>
          </button>

          {panelOpen && (
            <>
              {/* 点击遮罩关闭面板 */}
              <button
                aria-label="关闭"
                onClick={() => setPanelOpen(false)}
                className="fixed inset-0 z-10 cursor-default"
              />
              <div className="absolute right-0 z-20 mt-2 w-80 rounded-xl border border-white/60 bg-white/95 p-3 text-left shadow-xl backdrop-blur">
                <div className="mb-2 flex items-center justify-between px-1">
                  <span className="text-xs font-semibold text-slate-500">
                    优化历史（{history.length}）
                  </span>
                  {history.length > 0 && (
                    <button
                      onClick={clearHistory}
                      className="text-xs text-slate-400 transition hover:text-rose-500"
                    >
                      清空
                    </button>
                  )}
                </div>
                {history.length === 0 ? (
                  <p className="px-1 py-4 text-center text-xs text-slate-400">
                    还没有记录，完成一次优化后出现在这里
                  </p>
                ) : (
                  <ul className="max-h-80 space-y-1.5 overflow-y-auto">
                    {history.map((item) => (
                      <li key={item.id}>
                        <button
                          onClick={() => restoreItem(item)}
                          className="flex w-full items-center gap-3 rounded-lg border border-transparent p-2.5 text-left transition hover:border-violet-200 hover:bg-violet-50"
                        >
                          <span
                            className={`shrink-0 text-xl font-bold ${scoreColor(item.score)}`}
                          >
                            {item.score}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm text-slate-700">
                              {item.jdExcerpt}
                            </span>
                            <span className="block text-xs text-slate-400">
                              {formatDate(item.createdAt)}
                            </span>
                          </span>
                          <span className="shrink-0 text-xs text-indigo-500">
                            查看
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="mt-2 border-t border-slate-100 px-1 pt-2 text-center text-[10px] text-slate-400">
                  仅保存在浏览器本地，不会上传
                </p>
              </div>
            </>
          )}
        </div>
        )}

        <h1 className="bg-gradient-to-r from-indigo-600 via-purple-600 to-rose-500 bg-clip-text text-4xl font-extrabold tracking-tight text-transparent">
          AI 简历优化 Agent
        </h1>
        <p className="mt-3 text-sm text-slate-500">
          LangGraph 驱动 · 一键分析匹配度 · 对话式改写简历
        </p>
        {/* 能力标签：求职演示加分项 */}
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {[
            "LangGraph 智能编排",
            "ATS 多维评分",
            "RAG 语义检索",
            "三级缓存",
            "对话式改写",
          ].map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-violet-200/70 bg-white/70 px-3 py-1 text-[11px] font-medium text-violet-600 shadow-sm backdrop-blur transition hover:border-violet-400 hover:text-violet-700"
            >
              {tag}
            </span>
          ))}
        </div>
      </header>

      {/* 模式切换：三个模式一行显示（列数必须与按钮数一致，否则会换行） */}
      <div className="mx-auto mt-6 mb-2 grid w-fit grid-cols-3 rounded-full bg-white/60 p-1 shadow-sm backdrop-blur">
        <button
          onClick={() => setTab("quick")}
          className={`rounded-full px-7 py-1.5 text-sm font-medium transition ${
            tab === "quick"
              ? "bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow"
              : "text-slate-500 hover:text-indigo-600"
          }`}
        >
          快速优化
        </button>
        <button
          onClick={() => setTab("chat")}
          className={`rounded-full px-7 py-1.5 text-sm font-medium transition ${
            tab === "chat"
              ? "bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow"
              : "text-slate-500 hover:text-indigo-600"
          }`}
        >
          对话模式
        </button>
        <button
          onClick={() => setTab("match")}
          className={`rounded-full px-7 py-1.5 text-sm font-medium transition ${
            tab === "match"
              ? "bg-gradient-to-r from-indigo-500 to-violet-500 text-white shadow"
              : "text-slate-500 hover:text-indigo-600"
          }`}
        >
          智能匹配
        </button>
      </div>

      <p className="mx-auto mb-8 max-w-xl text-center text-xs leading-relaxed text-slate-400">
        {TAB_DESC[tab]}
      </p>

      {tab === "quick" ? (
        <QuickOptimize
          history={history}
          persistHistory={persistHistory}
          restoreRequest={restoreRequest}
        />
      ) : tab === "chat" ? (
        <Chat />
      ) : (
        <Match />
      )}
    </main>
  );
}
