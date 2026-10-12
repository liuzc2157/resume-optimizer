"use client";

import { useCallback, useEffect, useState } from "react";
import Chat from "./chat";
import QuickOptimize, { RestoreRequest } from "./quick-optimize";
import Match from "./match";
import { AuthScreen, UserChip, useAuth } from "./auth";
import { Brand } from "./brand";
import {
  HISTORY_KEY,
  HistoryItem,
  formatDate,
  scoreColor,
} from "./history-utils";

type Tab = "quick" | "chat" | "match";

const TAB_DESC: Record<Tab, string> = {
  quick: "已有明确 JD：粘贴简历 + 岗位描述，一键得出 ATS 多维评分、AI 分析与改写建议。",
  chat: "边聊边改：多轮对话追问细节，适合需要反复调整措辞、逐句打磨的场景。",
  match:
    "还没有明确 JD：先从岗位库按「岗位类别 + 经验等级」找出最匹配的岗位，再针对性优化；这里也能看缓存命中情况。",
};

const TAB_ICONS: Record<Tab, string> = {
  quick: "⚡",
  chat: "💬",
  match: "🎯",
};

const TAGS = [
  "ATS 多维评分",
  "AI 分析改写",
  "岗位智能匹配",
  "对话式打磨",
  "秒级缓存命中",
];

export default function Home() {
  const auth = useAuth();
  const [tab, setTab] = useState<Tab>("quick");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [panelOpen, setPanelOpen] = useState(false);
  const [restoreRequest, setRestoreRequest] = useState<RestoreRequest | null>(null);

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

  // 未完成初始化（校验本地 token / 拉取用户信息）
  if (!auth.ready) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-10 text-center text-sm text-slate-400">
        正在加载…
      </main>
    );
  }

  // 未登录：品牌 + 登录 / 注册（登录卡片自带品牌卖点区）
  if (!auth.user) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-8">
        <Brand />
        <AuthScreen onSuccess={() => void auth.refresh()} />
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      {/* 顶栏：品牌 + 历史入口 + 用户 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Brand />
        <div className="flex items-center gap-2">
          {tab === "quick" && (
            <div className="relative">
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
                <span className="text-[10px] text-slate-400">{panelOpen ? "▲" : "▼"}</span>
              </button>

              {panelOpen && (
                <>
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
                              <span className={`shrink-0 text-xl font-bold ${scoreColor(item.score)}`}>
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
                              <span className="shrink-0 text-xs text-indigo-500">查看</span>
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
          <UserChip auth={auth} />
        </div>
      </div>

      {/* Hero */}
      <header className="relative mt-12 text-center">
        <div className="inline-flex items-center gap-2 rounded-full border border-violet-200/70 bg-white/70 px-3.5 py-1 text-xs font-medium text-violet-600 shadow-sm backdrop-blur animate-fade-up">
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-violet-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-violet-500" />
          </span>
          AI 驱动 · 3 秒出具匹配报告
        </div>
        <h1
          className="mt-5 text-5xl font-black tracking-tight text-slate-900 sm:text-6xl animate-fade-up"
          style={{ animationDelay: "60ms" }}
        >
          简<span className="text-brand-gradient">遇</span>
        </h1>
        <p
          className="mx-auto mt-4 max-w-xl text-base text-slate-500 sm:text-lg animate-fade-up"
          style={{ animationDelay: "120ms" }}
        >
          让每一份简历，更接近理想 Offer
        </p>
        <div
          className="mt-6 flex flex-wrap items-center justify-center gap-2 animate-fade-up"
          style={{ animationDelay: "180ms" }}
        >
          {TAGS.map((tag) => (
            <span
              key={tag}
              className="rounded-full border border-violet-200/70 bg-white/70 px-3 py-1 text-[11px] font-medium text-violet-600 shadow-sm backdrop-blur transition hover:border-violet-400 hover:text-violet-700"
            >
              {tag}
            </span>
          ))}
        </div>
      </header>

      {/* 模式切换：三模式精致分段控件 */}
      <div className="mx-auto mt-9 grid w-fit grid-cols-3 rounded-2xl border border-white/60 bg-white/60 p-1.5 shadow-lg shadow-violet-200/40 backdrop-blur">
        {(["quick", "chat", "match"] as Tab[]).map((key) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-1.5 rounded-xl px-6 py-2 text-sm font-semibold transition ${
              tab === key
                ? "bg-brand-gradient text-white shadow-md shadow-violet-400/30"
                : "text-slate-500 hover:text-indigo-600"
            }`}
          >
            <span className="text-base leading-none">{TAB_ICONS[key]}</span>
            <span>
              {key === "quick" ? "快速优化" : key === "chat" ? "对话模式" : "智能匹配"}
            </span>
          </button>
        ))}
      </div>

      <p className="mx-auto mt-4 mb-8 max-w-xl text-center text-xs leading-relaxed text-slate-400">
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