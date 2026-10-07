"use client";

import { useEffect, useRef, useState } from "react";
import Markdown from "./markdown";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  cacheHit?: boolean; // 本轮分析是否命中缓存（仅 assistant 消息）
}

interface ChatSession {
  id: string; // thread_id，续聊时后端靠它恢复上下文
  title: string; // 第一条用户消息的摘要
  updatedAt: string;
  messages: ChatMessage[];
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const CHAT_HISTORY_KEY = "resume-optimizer-chat-history";
const CHAT_HISTORY_LIMIT = 20;

const SUGGESTIONS = [
  "我想优化简历，先帮我看看怎么开始",
  "简历写作有哪些常见的坑？",
  "怎么用数字量化项目经历？",
];

function newSessionId(): string {
  return typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random()}`;
}

function makeTitle(text: string): string {
  const t = text.trim().replace(/\s+/g, " ");
  return t.length > 24 ? `${t.slice(0, 24)}…` : t;
}

function loadSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(CHAT_HISTORY_KEY);
    return raw ? (JSON.parse(raw) as ChatSession[]) : [];
  } catch (error) {
    console.error(error);
    return [];
  }
}

const WELCOME: ChatMessage = {
  role: "assistant",
  content:
    "你好！我是简历优化 Agent 🤖\n\n你可以直接把简历发给我，再发目标岗位的 JD，我会帮你分析匹配度并给出改写建议。也可以随时问我简历写作的问题。",
};

export default function Chat() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [sessionId, setSessionId] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([WELCOME]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [showSessions, setShowSessions] = useState(false);
  const [showTarget, setShowTarget] = useState(false);
  const [positionCat, setPositionCat] = useState("");
  const [experienceLevel, setExperienceLevel] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // 首次挂载：加载历史会话列表（不自动展开旧对话，保持干净的欢迎页）
  useEffect(() => {
    setSessions(loadSessions());
    setSessionId(newSessionId());
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const persistSession = (id: string, msgs: ChatMessage[]) => {
    // 只保存有实际对话内容的会话（只有欢迎语的不存）
    if (msgs.filter((m) => m.role === "user").length === 0) return;
    const existing = loadSessions();
    const idx = existing.findIndex((s) => s.id === id);
    const entry: ChatSession = {
      id,
      title:
        idx >= 0
          ? existing[idx].title
          : makeTitle(msgs.find((m) => m.role === "user")?.content ?? "新对话"),
      updatedAt: new Date().toISOString(),
      messages: msgs,
    };
    const next =
      idx >= 0
        ? existing.map((s, i) => (i === idx ? entry : s))
        : [entry, ...existing];
    const trimmed = next.slice(0, CHAT_HISTORY_LIMIT);
    try {
      localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(trimmed));
      setSessions(trimmed);
    } catch (error) {
      console.error(error);
    }
  };

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", content }]);
    setLoading(true);

    try {
      const res = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: content,
          thread_id: sessionId,
          position_category: positionCat || undefined,
          experience_level: experienceLevel || undefined,
        }),
      });
      if (!res.ok) {
        throw new Error(`API returned ${res.status}`);
      }
      const data: { reply: string; cache_hit: boolean } = await res.json();
      setMessages((prev) => {
        const next = [
          ...prev,
          { role: "assistant", content: data.reply, cacheHit: data.cache_hit } as ChatMessage,
        ];
        persistSession(sessionId, next);
        return next;
      });
    } catch (error) {
      console.error(error);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "抱歉，出了点问题，请重试。" },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const openSession = (s: ChatSession) => {
    setSessionId(s.id); // 复用 thread_id，后端 Checkpointer 尚在时可无缝续聊
    setMessages(s.messages);
    setShowSessions(false);
  };

  const startNewSession = () => {
    setSessionId(newSessionId());
    setMessages([WELCOME]);
    setShowSessions(false);
  };

  const deleteSession = (id: string) => {
    const next = sessions.filter((s) => s.id !== id);
    try {
      localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(next));
    } catch (error) {
      console.error(error);
    }
    setSessions(next);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  };

  return (
    <div>
      {/* 会话管理条：历史对话 / 新建 */}
      <div className="mb-3 flex items-center justify-between">
        <button
          onClick={() => setShowSessions((v) => !v)}
          className="flex items-center gap-1.5 rounded-full border border-violet-200 bg-white/70 px-4 py-1.5 text-xs font-medium text-slate-600 shadow-sm backdrop-blur transition hover:border-violet-400 hover:text-violet-600"
        >
          历史对话
          {sessions.length > 0 && (
            <span className="rounded-full bg-gradient-to-r from-indigo-500 to-violet-500 px-1.5 py-0.5 text-[10px] font-bold text-white">
              {sessions.length}
            </span>
          )}
          <span className="text-[10px] text-slate-400">{showSessions ? "▲" : "▼"}</span>
        </button>
        <button
          onClick={startNewSession}
          className="rounded-full border border-indigo-200 bg-white/70 px-4 py-1.5 text-xs font-medium text-indigo-600 shadow-sm backdrop-blur transition hover:border-indigo-400"
        >
          ＋ 新建对话
        </button>
        <button
          onClick={() => setShowTarget((v) => !v)}
          className={`rounded-full border px-4 py-1.5 text-xs font-medium shadow-sm backdrop-blur transition ${
            showTarget
              ? "border-violet-400 bg-violet-50 text-violet-700"
              : "border-indigo-200 bg-white/70 text-slate-600 hover:border-violet-400"
          }`}
        >
          目标岗位
        </button>
      </div>

      {showTarget && (
        <div className="mb-3 grid grid-cols-2 gap-2 rounded-xl border border-white/60 bg-white/70 p-3 shadow-sm backdrop-blur">
          <input
            value={positionCat}
            onChange={(e) => setPositionCat(e.target.value)}
            placeholder="岗位类别（可选）"
            className="rounded-lg border border-indigo-200/70 bg-white/80 p-2 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
          />
          <input
            value={experienceLevel}
            onChange={(e) => setExperienceLevel(e.target.value)}
            placeholder="经验等级（可选）"
            className="rounded-lg border border-indigo-200/70 bg-white/80 p-2 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
          />
          <p className="col-span-2 text-[10px] text-slate-400">
            设定后，对话 Agent 在缺简历/JD 时可按岗位与经验智能匹配；命中缓存时回复会标注「⚡ 缓存命中」。
          </p>
        </div>
      )}

      {showSessions && (
        <div className="mb-3 rounded-xl border border-white/60 bg-white/95 p-2 shadow-md backdrop-blur">
          {sessions.length === 0 ? (
            <p className="py-3 text-center text-xs text-slate-400">还没有历史对话</p>
          ) : (
            <ul className="max-h-56 space-y-1 overflow-y-auto">
              {sessions.map((s) => (
                <li key={s.id} className="group flex items-center gap-2">
                  <button
                    onClick={() => openSession(s)}
                    className={`min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-left text-sm transition hover:bg-violet-50 ${
                      s.id === sessionId
                        ? "bg-violet-50 text-violet-700"
                        : "text-slate-700"
                    }`}
                  >
                    {s.title}
                  </button>
                  <button
                    onClick={() => deleteSession(s.id)}
                    className="shrink-0 rounded-md px-2 py-1 text-xs text-slate-300 opacity-0 transition group-hover:opacity-100 hover:text-rose-500"
                    title="删除该对话"
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div
        className="flex flex-col rounded-xl border border-white/60 bg-white/70 shadow-sm backdrop-blur"
        style={{ height: "65vh" }}
      >
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${
                  msg.role === "user"
                    ? "rounded-br-sm bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow"
                    : "rounded-bl-sm bg-white/90 text-slate-800 shadow-sm"
                }`}
              >
                {msg.role === "assistant" ? (
                  <>
                    {msg.cacheHit && (
                      <span className="mb-1.5 inline-block rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
                        ⚡ 缓存命中
                      </span>
                    )}
                    <Markdown>{msg.content}</Markdown>
                  </>
                ) : (
                  <span className="whitespace-pre-wrap text-sm leading-relaxed">
                    {msg.content}
                  </span>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex justify-start">
              <div className="rounded-2xl rounded-bl-sm bg-white/90 px-4 py-2.5 shadow-sm">
                <span className="flex gap-1">
                  <span className="h-2 w-2 animate-bounce rounded-full bg-violet-400" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-violet-400 [animation-delay:0.15s]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-violet-400 [animation-delay:0.3s]" />
                </span>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {messages.length <= 1 && (
          <div className="flex flex-wrap gap-2 px-4 pb-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => void send(s)}
                className="rounded-full border border-violet-200 bg-white/70 px-3 py-1.5 text-xs text-slate-600 transition hover:border-violet-400 hover:text-violet-600"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        <div className="border-t border-indigo-100 p-3">
          <div className="flex items-end gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息，Enter 发送，Shift+Enter 换行"
              className="flex-1 resize-none rounded-lg border border-indigo-200/70 bg-white/80 p-2.5 text-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
            />
            <button
              onClick={() => void send(input)}
              disabled={loading || !input.trim()}
              className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-4 py-2.5 text-sm font-medium text-white shadow transition hover:from-indigo-600 hover:to-violet-600 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
            >
              发送
            </button>
          </div>
        </div>
      </div>

      <p className="mt-2 text-center text-xs text-slate-400">
        对话记录保存在浏览器本地；后端服务重启后，继续旧对话时请重新提供简历/JD
      </p>
    </div>
  );
}
