"use client";

import { useEffect, useRef, useState } from "react";
import Markdown from "./markdown";
import { ApiError, apiGet, authFetch } from "./api";
import { HISTORY_LIMIT, HistoryItem, excerpt } from "./history-utils";

interface MatchedJD {
  jd_id: number;
  title: string;
  position_category: string;
  experience_level: string;
  similarity: number | null;
}

interface ScoreDimension {
  key: string;
  label: string;
  score: number;
  weight: number;
  detail: string;
  hits?: string[] | null;
  misses?: string[] | null;
}

interface OptimizeResponse {
  score: number;
  llm_score?: number | null;
  analysis: string;
  rewrite: string;
  cache_hit: boolean;
  cache_source: string;
  cache_similarity?: number;
  matched_jd?: MatchedJD | null;
  matches: MatchedJD[];
  breakdown?: { total: number; dimensions: ScoreDimension[] } | null;
  messages: string[];
}


interface ResumeItem {
  id: number;
  name: string | null;
  content: string;
}
export interface RestoreRequest {
  item: HistoryItem;
  seq: number;
}

interface Props {
  history: HistoryItem[];
  persistHistory: (items: HistoryItem[]) => void;
  restoreRequest: RestoreRequest | null;
}



// 三档改写强度：只影响改写 prompt，缓存互相隔离
const INTENSITY_OPTIONS = [
  { key: "light", label: "轻推", desc: "最小改动，只修明显问题" },
  { key: "enhance", label: "增强", desc: "强化关键词与量化成果" },
  { key: "rewrite", label: "重写", desc: "整篇重构，最大贴合 JD" },
] as const;
// 一键体验：参考 OpenResume 的示例数据填充，快速跑通全流程
const SAMPLE_RESUME = `张三
求职意向：后端开发工程师（Python）

工作经历
2021.07 - 至今 某科技有限公司 · 后端工程师
- 负责订单系统开发与维护，日请求量峰值约 200 万
- 主导支付模块重构，接口响应时间从 800ms 降至 120ms
- 技术栈：Python / FastAPI / MySQL / Redis / Docker

2020.06 - 2021.06 某互联网公司 · 后端实习生
- 参与用户中心微服务拆分，支撑 5 万并发登录

项目经历
订单中台（2022.03 - 2022.12）
- 设计异步对账流程，对账异常率下降 60%

技能
Python、FastAPI、Flask、MySQL、Redis、RabbitMQ、Docker、Git、Linux`;

const SAMPLE_JD = `岗位名称：后端开发工程师（Python）
岗位职责：
1. 负责核心业务系统的设计、开发与维护，保障系统稳定性与可用性
2. 与产品、前端协作完成需求评审与技术方案设计
3. 参与服务性能优化，持续提升接口响应速度与并发能力
任职要求：
1. 本科及以上学历，计算机相关专业，2 年以上后端开发经验
2. 熟练掌握 Python，熟悉 FastAPI / Flask / Django 任一框架
3. 熟悉 MySQL、Redis，理解常见缓存与消息队列的使用场景
4. 具备良好的代码习惯与团队协作能力`;

export default function QuickOptimize({
  history,
  persistHistory,
  restoreRequest,
}: Props) {
  const [resumeText, setResumeText] = useState("");
  const [jdText, setJdText] = useState("");
  const [intensity, setIntensity] = useState<string>("enhance");
  const [loading, setLoading] = useState(false);
  const [parsing, setParsing] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [myResumes, setMyResumes] = useState<ResumeItem[]>([]);
  const [selectedResumeId, setSelectedResumeId] = useState<number | null>(null);
  const [savingResume, setSavingResume] = useState(false);
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [copied, setCopied] = useState<"analysis" | "rewrite" | null>(null);
  const [showLog, setShowLog] = useState(false);

  // 可选：按岗位类别 + 经验等级让后端智能匹配岗位
  const [positionCat, setPositionCat] = useState("");
  const [experienceLevel, setExperienceLevel] = useState("");

  // 右上角历史面板点「查看」时恢复对应结果
  useEffect(() => {
    if (restoreRequest) {
      const { item } = restoreRequest;
      setResult({
        score: item.score,
        analysis: item.analysis,
        rewrite: item.rewrite,
        cache_hit: false,
        cache_source: "restored",
        matches: [],
        messages: item.messages,
      });
      setShowLog(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }, [restoreRequest]);
// 加载我的简历库（用于快速调用 + 保存/覆盖）
  useEffect(() => {
    let alive = true;
    apiGet<ResumeItem[]>("/resumes")
      .then((data) => {
        if (!alive) return;
        setMyResumes(data);
        // 简历内容为空时，自动填入最近更新的简历，省去手动粘贴/选择
        if (data.length > 0) {
          setSelectedResumeId(data[0].id);
          setResumeText((prev) => {
            if (prev && prev.trim()) return prev;
            return data[0].content;
          });
        }
      })
      .catch((error) => console.error(error));
    return () => {
      alive = false;
    };
  }, []);

  const handleOptimize = async () => {
    if (!resumeText.trim() || !jdText.trim()) {
      alert("请先填写简历和 JD");
      return;
    }

    setLoading(true);
    setResult(null);
    setShowLog(false);

    try {
      const res = await authFetch("/optimize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          jd_text: jdText || undefined,
          position_category: positionCat || undefined,
          experience_level: experienceLevel || undefined,
          intensity,
        }),
      });

      if (!res.ok) {
        throw new Error(`API returned ${res.status}`);
      }

      const data: OptimizeResponse = await res.json();
      setResult(data);

      // 保存到本地历史（最新在前，超出上限淘汰最旧的）
      const item: HistoryItem = {
        ...data,
        id: `${Date.now()}`,
        createdAt: new Date().toISOString(),
        jdExcerpt: excerpt(jdText),
      };
      persistHistory([item, ...history].slice(0, HISTORY_LIMIT));
    } catch (error) {
      console.error(error);
      alert(error instanceof ApiError ? error.message : "优化失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  const handleCopy = async (field: "analysis" | "rewrite") => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(result[field]);
      setCopied(field);
      setTimeout(() => setCopied(null), 1500);
    } catch (error) {
      console.error(error);
      alert("复制失败，请手动选择文本复制");
    }
  };

const saveResume = async () => {
    const text = resumeText.trim();
    if (!text) return;
    setSavingResume(true);
    try {
      if (selectedResumeId) {
        await authFetch(`/resumes/${selectedResumeId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text }),
        });
        alert("已覆盖更新到我的简历 ✓");
      } else {
        const res = await authFetch("/resumes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text }),
        });
        const created = (await res.json()) as ResumeItem;
        setSelectedResumeId(created.id);
        alert("已保存到我的简历 ✓（可在简历页改名）");
      }
      const data = await apiGet<ResumeItem[]>("/resumes");
      setMyResumes(data);
    } catch (error) {
      console.error(error);
      alert(error instanceof ApiError ? error.message : "保存失败，请重试");
    } finally {
      setSavingResume(false);
    }
  };

  const fillSample = () => {
    setResumeText(SAMPLE_RESUME);
    setJdText(SAMPLE_JD);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleUploadFile = async (file: File) => {
    setParsing(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await authFetch("/resume/parse", { method: "POST", body: form });
      const data = await res.json();
      setResumeText(data.text);
    } catch (error) {
      console.error(error);
      alert(error instanceof ApiError ? error.message : "文件解析失败，请直接粘贴文本");
    } finally {
      setParsing(false);
    }
  };

  const exportMarkdown = () => {
    if (!result) return;
    const lines = [
      "# 简历优化报告",
      "",
      `- ATS 多维评分：**${result.score}**`,
      `- 缓存命中：${result.cache_hit ? "是" : "否"}`,
      "",
      "## 匹配度分析",
      "",
      result.analysis,
      "",
      "## 优化建议",
      "",
      result.rewrite,
      "",
    ];
    const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `简历优化报告-${new Date().toISOString().slice(0, 10)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div>
      <section className="space-y-5">
        <div>
          <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
            <label htmlFor="resume" className="text-sm font-medium text-slate-700">
              简历内容
            </label>
            <div className="flex items-center gap-2">
              {myResumes.length > 0 && (
                <select
                  value={selectedResumeId ?? ""}
                  onChange={(e) => {
                    const id = Number(e.target.value);
                    setSelectedResumeId(id || null);
                    const found = myResumes.find((r) => r.id === id);
                    if (found) setResumeText(found.content);
                  }}
                  className="rounded-md border border-violet-200 bg-white/70 px-2 py-0.5 text-[11px] text-violet-600 shadow-sm outline-none transition focus:border-violet-400"
                >
                  <option value="">我的简历…</option>
                  {myResumes.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name ?? "未命名简历"}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                onClick={fillSample}
                className="rounded-md border border-violet-200 bg-white/70 px-2 py-0.5 text-[11px] text-violet-600 shadow-sm transition hover:border-violet-400 hover:bg-violet-50"
              >
                ✨ 用示例试试
              </button>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={parsing}
                className="rounded-md border border-violet-200 bg-white/70 px-2 py-0.5 text-[11px] text-violet-600 shadow-sm transition hover:border-violet-400 hover:bg-violet-50 disabled:opacity-50"
              >
                {parsing ? "解析中…" : "📄 上传文件"}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.md,.markdown,.pdf,.docx"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void handleUploadFile(f);
                  e.target.value = "";
                }}
              />
              <span className="text-xs text-slate-400">{resumeText.length} 字</span>
            </div>
          </div>
          <textarea
            id="resume"
            rows={10}
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
            placeholder="粘贴你的简历全文（纯文本即可）"
            className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-3 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
          />
        </div>

        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <label htmlFor="jd" className="text-sm font-medium text-slate-700">
              目标岗位 JD
            </label>
            <span className="text-xs text-slate-400">{jdText.length} 字</span>
          </div>
          <textarea
            id="jd"
            rows={6}
            value={jdText}
            onChange={(e) => setJdText(e.target.value)}
            placeholder="粘贴目标岗位的职位描述（留空则按下方岗位维度智能匹配）"
            className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-3 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">
              岗位类别（可选）
            </label>
            <input
              list="position-cats"
              value={positionCat}
              onChange={(e) => setPositionCat(e.target.value)}
              placeholder="如 后端 / 前端 / 算法"
              className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-2.5 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700">
              经验等级（可选）
            </label>
            <input
              list="experience-levels"
              value={experienceLevel}
              onChange={(e) => setExperienceLevel(e.target.value)}
              placeholder="如 初级 / 中级 / 高级"
              className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-2.5 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
            />
          </div>
          <datalist id="position-cats">
            {["后端", "前端", "算法", "数据", "测试", "产品"].map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
          <datalist id="experience-levels">
            {["初级", "中级", "高级", "资深"].map((l) => (
              <option key={l} value={l} />
            ))}
          </datalist>
        </div>

        {/* 三档改写强度：只影响改写结果，缓存互相隔离 */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700">
            改写强度
          </label>
          <div className="grid grid-cols-3 gap-2">
            {INTENSITY_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => setIntensity(opt.key)}
                className={`rounded-lg border p-2.5 text-left transition ${
                  intensity === opt.key
                    ? "border-violet-500 bg-violet-50 shadow-sm"
                    : "border-indigo-200/70 bg-white/70 hover:border-violet-300"
                }`}
              >
                <span
                  className={`block text-sm font-semibold ${
                    intensity === opt.key ? "text-violet-700" : "text-slate-700"
                  }`}
                >
                  {opt.label}
                </span>
                <span className="mt-0.5 block text-[11px] leading-snug text-slate-400">
                  {opt.desc}
                </span>
              </button>
            ))}
          </div>
        </div>

        <button
          onClick={handleOptimize}
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 py-3 font-medium text-white shadow-md transition hover:from-indigo-600 hover:to-violet-600 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
        >
          {loading && (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent" />
          )}
          {loading ? "优化中..." : "开始优化"}
        </button>
        <p className="text-center text-xs text-slate-400">
          系统将依次执行：输入解析 → 匹配度分析 → 简历改写 → 结果汇总
        </p>
      </section>

      {result && (
        <section className="mt-10 space-y-6">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              onClick={() => void saveResume()}
              disabled={savingResume || !resumeText.trim()}
              className="rounded-md border border-violet-200 bg-white/70 px-3 py-1.5 text-xs text-violet-600 shadow-sm transition hover:border-violet-400 hover:bg-violet-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {savingResume
                ? "保存中…"
                : selectedResumeId
                  ? "覆盖到我的简历"
                  : "保存到我的简历"}
            </button>
            <button
              onClick={exportMarkdown}
              className="rounded-md border border-violet-200 bg-white/70 px-3 py-1.5 text-xs text-violet-600 shadow-sm transition hover:border-violet-400 hover:bg-violet-50"
            >
              ⬇ 导出 Markdown
            </button>
          </div>
          <div className="rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-purple-500 p-6 text-center shadow-lg">
            <p className="text-sm text-indigo-100">ATS 多维评分（规则计算）</p>
            <p className="mt-1 text-6xl font-extrabold text-white">{result.score}</p>
            {result.llm_score != null && result.llm_score !== result.score && (
              <p className="mt-1 text-xs text-indigo-100/90">
                AI 整体评分 {result.llm_score} · 两者口径不同，ATS 分看硬指标，AI 分偏主观
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center justify-center gap-2">
              {result.cache_hit ? (
                <span className="rounded-full bg-amber-300/90 px-3 py-1 text-xs font-semibold text-amber-900">
                  ⚡ 缓存命中（{result.cache_source}）
                  {result.cache_source === "semantic" && result.cache_similarity
                    ? ` 相似度 ${(result.cache_similarity * 100).toFixed(1)}%`
                    : ""}
                </span>
              ) : (
                <span className="rounded-full bg-white/25 px-3 py-1 text-xs font-semibold text-white">
                  实时生成
                </span>
              )}
              {result.matched_jd && (
                <span className="rounded-full bg-white/20 px-3 py-1 text-xs font-medium text-white">
                  匹配岗位：{result.matched_jd.title}
                </span>
              )}
            </div>
          </div>

          {result.breakdown?.dimensions?.length ? (
            <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-900">多维度评分明细</h2>
                <span className="text-xs text-slate-400">规则计算 · 可解释可改进</span>
              </div>

              {/* 雷达图 + 条形图：同一份数据两种视角 */}
              <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
                <RadarChart dims={result.breakdown.dimensions} />
                <div className="min-w-0 space-y-4">
                  {result.breakdown.dimensions.map((d) => (
                    <div key={d.key}>
                      <div className="flex items-baseline justify-between text-sm">
                        <span className="font-medium text-slate-700">
                          {d.label}
                          <span className="ml-1.5 text-xs font-normal text-slate-400">
                            权重 {Math.round(d.weight * 100)}%
                          </span>
                        </span>
                        <span className="font-semibold text-slate-800">{d.score}</span>
                      </div>
                      <div className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-slate-200">
                        <div
                          className={`h-full rounded-full transition-all ${
                            d.score >= 80
                              ? "bg-emerald-500"
                              : d.score >= 60
                                ? "bg-amber-500"
                                : "bg-rose-400"
                          }`}
                          style={{ width: `${Math.max(2, Math.min(100, d.score))}%` }}
                        />
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-slate-500">{d.detail}</p>

                      {/* 关键词命中高亮：命中绿色、缺失红色 */}
                      {(d.hits?.length || d.misses?.length) ? (
                        <div className="mt-1.5 flex flex-wrap gap-1">
                          {d.hits?.map((k) => (
                            <span
                              key={k}
                              className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700"
                            >
                              ✓ {k}
                            </span>
                          ))}
                          {d.misses?.map((k) => (
                            <span
                              key={k}
                              className="rounded bg-rose-100 px-1.5 py-0.5 text-[10px] font-medium text-rose-600"
                            >
                              ✗ {k}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : null}

          <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">分析报告</h2>
              <button
                onClick={() => handleCopy("analysis")}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-100"
              >
                {copied === "analysis" ? "已复制 ✓" : "复制"}
              </button>
            </div>
            <Markdown>{result.analysis}</Markdown>
          </div>

          <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">优化建议</h2>
              <button
                onClick={() => handleCopy("rewrite")}
                className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-100"
              >
                {copied === "rewrite" ? "已复制 ✓" : "复制"}
              </button>
            </div>
            <Markdown>{result.rewrite}</Markdown>
          </div>

          {result.messages.length > 0 && (
            <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
              <button
                onClick={() => setShowLog((v) => !v)}
                className="flex w-full items-center justify-between text-left"
              >
                <h2 className="text-sm font-semibold text-slate-500">
                  运行日志（{result.messages.length} 条）
                </h2>
                <span className="text-xs text-slate-400">
                  {showLog ? "收起 ▲" : "展开 ▼"}
                </span>
              </button>
              {showLog && (
                <ul className="mt-3 space-y-1 font-mono text-xs text-slate-500">
                  {result.messages.map((msg, i) => (
                    <li key={i} className="border-l-2 border-violet-300 pl-3">
                      {msg}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

// 五维雷达图（纯 SVG 手绘，无第三方依赖）：轴标签用两字缩写避免拥挤
const RADAR_LABELS: Record<string, string> = {
  keyword_match: "关键词",
  experience_match: "经验",
  quantification: "量化",
  action_verbs: "动词",
  completeness: "结构",
};

function RadarChart({ dims }: { dims: ScoreDimension[] }) {
  const n = dims.length;
  if (n < 3) return null;
  const SIZE = 230;
  const CX = 115;
  const CY = 118;
  const R = 78;
  const pt = (i: number, r: number): [number, number] => {
    const ang = (Math.PI * 2 * i) / n - Math.PI / 2;
    return [CX + r * Math.cos(ang), CY + r * Math.sin(ang)];
  };
  const rings = [25, 50, 75, 100];
  const ringPaths = rings.map((pct) => {
    const pts = dims.map((_, i) => pt(i, (R * pct) / 100).join(","));
    return pts.join(" ");
  });
  const dataPts = dims.map((d, i) => pt(i, (Math.max(0, Math.min(100, d.score)) / 100) * R).join(","));
  const dataPoly = dataPts.join(" ");
  const scale = Math.min(0.9, (SIZE * 0.86) / (CX + R + 46));
  return (
    <div className="mx-auto w-fit">
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        style={{ transform: `scale(${scale})`, transformOrigin: "center" }}
      >
        {/* 刻度网格 */}
        {ringPaths.map((d) => (
          <polygon key={d} points={d} fill="none" stroke="#e2e8f0" strokeWidth={1} />
        ))}
        {/* 轴连线 */}
        {dims.map((_, i) => {
          const [x, y] = pt(i, R);
          return <line key={i} x1={CX} y1={CY} x2={x} y2={y} stroke="#e2e8f0" strokeWidth={1} />;
        })}
        {/* 数据多边形 */}
        <polygon
          points={dataPoly}
          fill="rgba(139,92,246,0.25)"
          stroke="#8b5cf6"
          strokeWidth={2}
          strokeLinejoin="round"
        />
        {/* 数据点 */}
        {dims.map((d, i) => {
          const [x, y] = pt(i, (Math.max(0, Math.min(100, d.score)) / 100) * R);
          return <circle key={i} cx={x} cy={y} r={3.5} fill="#8b5cf6" />;
        })}
        {/* 轴标签 + 分值 */}
        {dims.map((d, i) => {
          const [x, y] = pt(i, R + 24);
          const anchor = Math.abs(x - CX) < 6 ? "middle" : x > CX ? "start" : "end";
          return (
            <g key={d.key}>
              <text x={x} y={y} textAnchor={anchor} fontSize={11} fill="#64748b" fontWeight={600}>
                {RADAR_LABELS[d.key] ?? d.label.slice(0, 2)}
              </text>
              <text x={x} y={y + 13} textAnchor={anchor} fontSize={10} fill="#94a3b8">
                {Math.round(d.score)}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="mt-1 text-center text-[10px] text-slate-400">五维雷达图 · 满分 100</p>
    </div>
  );
}
