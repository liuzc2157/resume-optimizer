"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "./markdown";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface MatchedJD {
  jd_id: number;
  title: string;
  position_category: string;
  experience_level: string;
  similarity: number | null;
}

interface JDItem {
  id: number;
  position_category: string;
  experience_level: string;
  title: string;
  content: string;
  skills: string[] | null;
  source?: string | null; // seed=内置示例；user=用户自己保存的
}

interface OptimizeResult {
  score: number;
  analysis: string;
  rewrite: string;
  cache_hit: boolean;
  cache_source: string;
  matched_jd?: MatchedJD | null;
  matches: MatchedJD[];
  messages: string[];
  breakdown?: { total: number; dimensions: ScoreDimension[] } | null;
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

// 三档改写强度：与快速优化页一致
const INTENSITY_OPTIONS = [
  { key: "light", label: "轻推", desc: "最小改动" },
  { key: "enhance", label: "增强", desc: "强化关键词" },
  { key: "rewrite", label: "重写", desc: "整篇重构" },
] as const;

interface CacheStats {
  hits: number;
  semantic_hits: number;
  misses: number;
  hit_rate: number;
  memory_size: number;
  total: number;
  threshold: number;
  semantic_enabled: boolean;
}

// 默认下拉选项（首次 /jds 返回空时兜底）
const DEFAULT_CATS = ["后端", "前端", "算法", "AI", "数据", "测试", "运维", "移动端", "安全"];
const DEFAULT_LEVELS = ["初级", "中级", "高级", "资深"];

interface RagItem {
  jd_id: number;
  title: string;
  position_category: string;
  experience_level: string;
  similarity: number;
  rag_text: string;
}

interface RagRetrieveResponse {
  query_text: string;
  items: RagItem[];
}

interface CompareItem {
  jd_id: number;
  title: string;
  position_category: string;
  experience_level: string;
  score: number;
  dimensions?: unknown[];
  llm_score?: number | null;
  cache_hit?: boolean;
}

interface CompareResponse {
  items: CompareItem[];
  best_jd_id: number | null;
}

function similarityColor(sim: number | null): string {
  if (sim == null) return "bg-slate-300";
  if (sim >= 0.7) return "bg-emerald-500";
  if (sim >= 0.4) return "bg-amber-500";
  return "bg-rose-400";
}

export default function Match() {
  const [resumeText, setResumeText] = useState("");

  // 维度选项（从岗位库动态派生，兜底用默认列表）
  const [cats, setCats] = useState<string[]>(DEFAULT_CATS);
  const [levels, setLevels] = useState<string[]>(DEFAULT_LEVELS);
  const [selCat, setSelCat] = useState("");
  const [selLevel, setSelLevel] = useState("");

  // 智能匹配结果
  const [matches, setMatches] = useState<MatchedJD[]>([]);
  const [matchLoading, setMatchLoading] = useState(false);

  // RAG 语义向量检索结果（useRag 开启时走 /rag/retrieve）
  const [useRag, setUseRag] = useState(false);
  const [ragResult, setRagResult] = useState<RagRetrieveResponse | null>(null);
  const [ragLoading, setRagLoading] = useState(false);

  // 岗位库
  const [jds, setJds] = useState<JDItem[]>([]);
  const [libLoading, setLibLoading] = useState(false);
  // 展开查看 JD 详情的岗位 id
  const [expandedJd, setExpandedJd] = useState<number | null>(null);
  // 岗位库大类折叠：当前展开的分类（null=全部收起）
  const [expandedCat, setExpandedCat] = useState<string | null>(null);
  // 三档改写强度
  const [intensity, setIntensity] = useState<string>("enhance");
  // 上传 JD 文件
  const [uploading, setUploading] = useState(false);
  // 多岗位横向对比
  const [compareIds, setCompareIds] = useState<number[]>([]);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareResult, setCompareResult] = useState<CompareResponse | null>(null);

  // 选中岗位 -> 优化结果
  const [selectedJd, setSelectedJd] = useState<JDItem | null>(null);
  const [result, setResult] = useState<OptimizeResult | null>(null);
  const [optLoading, setOptLoading] = useState(false);
  const [copied, setCopied] = useState<"analysis" | "rewrite" | null>(null);

  // 缓存命中率看板
  const [stats, setStats] = useState<CacheStats | null>(null);

  const resultRef = useRef<HTMLDivElement>(null);

  // 拉取岗位库，并派生维度选项
  const loadJds = useCallback(async () => {
    setLibLoading(true);
    try {
      const qs = new URLSearchParams();
      if (selCat) qs.set("position_category", selCat);
      if (selLevel) qs.set("experience_level", selLevel);
      const res = await fetch(`${API_URL}/jds?${qs.toString()}`);
      if (!res.ok) throw new Error(`/jds ${res.status}`);
      const data: JDItem[] = await res.json();
      setJds(data);
      if (data.length > 0) {
        setCats(Array.from(new Set([...DEFAULT_CATS, ...data.map((d) => d.position_category)])));
        setLevels(Array.from(new Set([...DEFAULT_LEVELS, ...data.map((d) => d.experience_level)])));
      }
    } catch (error) {
      console.error(error);
    } finally {
      setLibLoading(false);
    }
  }, [selCat, selLevel]);

  // 缓存命中率（进入页面拉一次 + 每 12 秒自动刷新）
  const loadStats = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/cache/stats`);
      if (res.ok) setStats(await res.json());
    } catch (error) {
      console.error(error);
    }
  }, []);

  useEffect(() => {
    void loadJds();
    void loadStats();
    const t = setInterval(() => void loadStats(), 12000);
    return () => clearInterval(t);
  }, [loadJds, loadStats]);

  const runMatch = async () => {
    if (!resumeText.trim()) {
      alert("请先填写简历内容");
      return;
    }
    // RAG 语义向量检索模式：走 /rag/retrieve，返回带召回解释的语义结果
    if (useRag) {
      setRagLoading(true);
      setRagResult(null);
      try {
        const res = await fetch(`${API_URL}/rag/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            resume_text: resumeText,
            top_k: 5,
            category: selCat || undefined,
            level: selLevel || undefined,
          }),
        });
        if (!res.ok) throw new Error(`/rag/retrieve ${res.status}`);
        const data: RagRetrieveResponse = await res.json();
        setRagResult(data);
      } catch (error) {
        console.error(error);
        alert("RAG 语义检索失败，请确认后端已连接数据库");
      } finally {
        setRagLoading(false);
      }
      return;
    }
    // 关键词匹配模式：走 /match（确定性规则，零额外成本）
    setMatchLoading(true);
    setMatches([]);
    try {
      const res = await fetch(`${API_URL}/match`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          position_category: selCat || undefined,
          experience_level: selLevel || undefined,
          top_n: 5,
        }),
      });
      if (!res.ok) throw new Error(`/match ${res.status}`);
      const data: { matches: MatchedJD[] } = await res.json();
      setMatches(data.matches);
    } catch (error) {
      console.error(error);
      alert("智能匹配失败，请确认后端已连接数据库");
    } finally {
      setMatchLoading(false);
    }
  };

  // 选中岗位库中的某个 JD -> 直接拿它做优化
  const useJd = async (jd: JDItem) => {
    setSelectedJd(jd);
    await runOptimize({ jd_id: jd.id });
  };

  // 按所选维度自动匹配并优化（不指定具体 JD）
  const autoOptimize = async () => {
    await runOptimize({
      position_category: selCat || undefined,
      experience_level: selLevel || undefined,
    });
  };

  const runOptimize = async (extra: {
    jd_id?: number;
    position_category?: string;
    experience_level?: string;
  }) => {
    if (!resumeText.trim()) {
      alert("请先填写简历内容");
      return;
    }
    setOptLoading(true);
    setResult(null);
    try {
      const res = await fetch(`${API_URL}/optimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          jd_id: extra.jd_id,
          position_category: extra.position_category,
          experience_level: extra.experience_level,
          intensity,
        }),
      });
      if (!res.ok) throw new Error(`/optimize ${res.status}`);
      const data: OptimizeResult = await res.json();
      setResult(data);
      resultRef.current?.scrollIntoView({ behavior: "smooth" });
      void loadStats(); // 优化后立即刷新命中率
    } catch (error) {
      console.error(error);
      alert("优化失败，请重试");
    } finally {
      setOptLoading(false);
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

  // 上传 JD 文件：解析后存进岗位库，刷新列表
  const uploadJdFile = async (file: File) => {
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_URL}/jds/upload`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const msg = await res.text().catch(() => "");
        throw new Error(`/jds/upload ${res.status} ${msg}`);
      }
      await res.json();
      await loadJds();
    } catch (error) {
      console.error(error);
      alert("JD 上传失败，请确认文件内容或后端已连接");
    } finally {
      setUploading(false);
    }
  };

  // 多岗位横向对比（确定性 ATS 分，零成本，秒出）
  const runCompare = async () => {
    if (compareIds.length === 0) return;
    if (!resumeText.trim()) {
      alert("请先填写简历内容");
      return;
    }
    setCompareLoading(true);
    setCompareResult(null);
    try {
      const res = await fetch(`${API_URL}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          jd_ids: compareIds,
          with_llm: false,
        }),
      });
      if (!res.ok) throw new Error(`/compare ${res.status}`);
      const data: CompareResponse = await res.json();
      setCompareResult(data);
    } catch (error) {
      console.error(error);
      alert("横向对比失败，请确认后端已连接数据库");
    } finally {
      setCompareLoading(false);
    }
  };

  const ratePct = stats ? Math.round(stats.hit_rate * 100) : 0;

  // 岗位库按大类分组（数量降序，同量按名称），用于分类折叠展示
  const groupedJds = useMemo(() => {
    const map = new Map<string, JDItem[]>();
    for (const jd of jds) {
      const cat = jd.position_category || "未分类";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(jd);
    }
    return Array.from(map.entries()).sort(
      (a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0], "zh")
    );
  }, [jds]);

  return (
    <div className="space-y-8">
      {/* 缓存命中率看板 */}
      <section className="rounded-xl border border-white/60 bg-white/70 p-5 shadow-sm backdrop-blur">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">缓存命中看板</h2>
          {stats && (
            <span className="text-xs text-slate-400">
              进程内热缓存 {stats.memory_size} 条 · 每 12s 自动刷新
            </span>
          )}
        </div>
        {stats ? (
          <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="精确命中" value={stats.hits} tone="emerald" />
            <Stat label="语义命中" value={stats.semantic_hits ?? 0} tone="violet" />
            <Stat label="未命中" value={stats.misses} tone="rose" />
            <Stat label="命中率" value={`${ratePct}%`} tone={ratePct >= 50 ? "violet" : "amber"} />
          </div>
        ) : (
          <p className="mt-3 text-xs text-slate-400">加载中…（需后端已连接数据库）</p>
        )}
        {stats && (
          <p className="mt-3 text-xs leading-relaxed text-slate-400">
            三级缓存：精确键 → 语义近似（同岗位分区内，阈值{" "}
            {stats.semantic_enabled ? stats.threshold : "未启用"}
            ）→ 调用 LLM。语义命中表示简历改了几个字仍能复用上次分析。
          </p>
        )}
      </section>

      {/* 简历 + 维度选择 */}
      <section className="space-y-4">
        <div>
          <div className="mb-1 flex items-baseline justify-between">
            <label className="text-sm font-medium text-slate-700">简历内容</label>
            <span className="text-xs text-slate-400">{resumeText.length} 字</span>
          </div>
          <textarea
            rows={8}
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
            placeholder="粘贴你的简历全文，系统将按岗位与经验智能匹配最合适的 JD"
            className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-3 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <SelectField
            label="岗位类别（可选）"
            value={selCat}
            onChange={setSelCat}
            options={cats}
            placeholder="不限"
          />
          <SelectField
            label="经验等级（可选）"
            value={selLevel}
            onChange={setSelLevel}
            options={levels}
            placeholder="不限"
          />
        </div>

        {/* 三档改写强度：与快速优化页一致，缓存互相隔离 */}
        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700">改写强度</label>
          <div className="grid grid-cols-3 gap-2">
            {INTENSITY_OPTIONS.map((opt) => (
              <button
                key={opt.key}
                type="button"
                onClick={() => setIntensity(opt.key)}
                className={`rounded-lg border p-2 text-left transition ${
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
                <span className="mt-0.5 block text-[11px] text-slate-400">{opt.desc}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={runMatch}
            disabled={matchLoading || ragLoading}
            className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2.5 text-sm font-medium text-white shadow transition hover:from-indigo-600 hover:to-violet-600 disabled:from-slate-300 disabled:to-slate-300"
          >
            {(matchLoading || ragLoading) ? "匹配中…" : "① 智能匹配岗位"}
          </button>
          <label className="flex cursor-pointer select-none items-center gap-2 text-xs text-slate-500">
            <input
              type="checkbox"
              checked={useRag}
              onChange={(e) => setUseRag(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-violet-600 focus:ring-violet-400"
            />
            语义向量检索（RAG）
          </label>
          <button
            onClick={autoOptimize}
            disabled={optLoading}
            className="rounded-lg border border-violet-300 bg-white/70 px-5 py-2.5 text-sm font-medium text-violet-600 shadow-sm backdrop-blur transition hover:border-violet-500 hover:text-violet-700 disabled:opacity-50"
          >
            {optLoading ? "优化中…" : "② 按维度自动匹配并优化"}
          </button>
        </div>
      </section>

      {/* 智能匹配结果 */}
      {matches.length > 0 && (
        <section>
          <h2 className="mb-3 text-sm font-semibold text-slate-700">
            匹配结果（按相似度排序）
          </h2>
          <div className="space-y-2">
            {matches.map((m, i) => (
              <div
                key={m.jd_id}
                className="flex items-center gap-3 rounded-lg border border-white/60 bg-white/70 p-3 shadow-sm backdrop-blur"
              >
                <span className="shrink-0 rounded-md bg-gradient-to-br from-indigo-500 to-violet-500 px-2 py-1 text-xs font-bold text-white">
                  #{i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-800">
                      {m.title}
                    </span>
                    <Tag text={m.position_category} />
                    <Tag text={m.experience_level} />
                  </div>
                  <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={`h-full rounded-full ${similarityColor(m.similarity)}`}
                      style={{ width: `${Math.round((m.similarity ?? 0) * 100)}%` }}
                    />
                  </div>
                </div>
                <span className="shrink-0 text-sm font-semibold text-slate-600">
                  {Math.round((m.similarity ?? 0) * 100)}%
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* RAG 语义召回结果（开启「语义向量检索」后展示） */}
      {ragResult && (
        <section>
          <h2 className="mb-3 text-sm font-semibold text-slate-700">
            语义召回结果（RAG · 按向量相似度排序）
          </h2>
          {ragResult.query_text && (
            <div className="mb-3 rounded-lg border border-indigo-200 bg-indigo-50/60 p-3 text-xs leading-relaxed text-slate-600">
              <span className="font-semibold text-indigo-700">检索查询提炼：</span>
              {ragResult.query_text}
            </div>
          )}
          <div className="space-y-2">
            {ragResult.items.map((m, i) => (
              <div
                key={m.jd_id}
                className="rounded-lg border border-white/60 bg-white/70 p-3 shadow-sm backdrop-blur"
              >
                <div className="flex items-center gap-3">
                  <span className="shrink-0 rounded-md bg-gradient-to-br from-indigo-500 to-violet-500 px-2 py-1 text-xs font-bold text-white">
                    #{i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-slate-800">
                        {m.title}
                      </span>
                      <Tag text={m.position_category} />
                      <Tag text={m.experience_level} />
                      <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
                        RAG
                      </span>
                    </div>
                    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                      <div
                        className={`h-full rounded-full ${similarityColor(m.similarity)}`}
                        style={{ width: `${Math.round((m.similarity ?? 0) * 100)}%` }}
                      />
                    </div>
                  </div>
                  <span className="shrink-0 text-sm font-semibold text-slate-600">
                    {Math.round((m.similarity ?? 0) * 100)}%
                  </span>
                </div>
                {m.rag_text && (
                  <p className="mt-2 rounded-lg bg-slate-50 p-2.5 text-[11px] leading-relaxed text-slate-500">
                    <span className="font-medium text-slate-600">召回依据：</span>
                    {m.rag_text}
                  </p>
                )}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* 岗位库浏览 */}
      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">
            岗位库（{jds.length} 个 · 点「JD 详情」看介绍，点下方按钮直接优化）
          </h2>
          <div className="flex items-center gap-3">
            {/* 上传 JD 文件：解析后存进岗位库，下次可直接复用 */}
            <label className="cursor-pointer text-xs text-violet-600 transition hover:text-violet-700">
              {uploading ? "上传中…" : "↑ 上传 JD"}
              <input
                type="file"
                accept=".txt,.md,.pdf,.docx"
                className="hidden"
                disabled={uploading}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void uploadJdFile(f);
                  e.target.value = "";
                }}
              />
            </label>
            <button
              onClick={() => void loadJds()}
              className="text-xs text-slate-400 transition hover:text-violet-600"
            >
              {libLoading ? "刷新中…" : "↻ 刷新"}
            </button>
          </div>
        </div>

        {/* 勾选多个岗位后横向对比 */}
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-white/60 bg-white/60 px-3 py-2">
          <span className="text-xs text-slate-500">
            已勾选 {compareIds.length} 个岗位
          </span>
          <button
            onClick={() => void runCompare()}
            disabled={compareIds.length === 0 || compareLoading || !resumeText.trim()}
            className="rounded-md bg-gradient-to-r from-indigo-500 to-violet-500 px-3 py-1 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-40"
          >
            {compareLoading ? "对比中…" : "横向对比（不调 AI，秒出）"}
          </button>
          {compareIds.length > 0 && (
            <button
              onClick={() => setCompareIds([])}
              className="text-xs text-slate-400 transition hover:text-rose-500"
            >
              清空勾选
            </button>
          )}
          {compareIds.length > 0 && !resumeText.trim() && (
            <span className="text-xs text-amber-600">请先填写上方简历内容</span>
          )}
        </div>

        {compareResult && compareResult.items.length > 0 && (
          <div className="mb-3 rounded-lg border border-violet-200 bg-violet-50/60 p-3">
            <p className="mb-2 text-xs font-semibold text-slate-700">
              对比结果（按 ATS 分排序 · 最高分最值得投）
            </p>
            <div className="space-y-1.5">
              {compareResult.items.map((it, idx) => (
                <div key={it.jd_id} className="flex items-center gap-2 text-xs">
                  <span
                    className={`w-5 shrink-0 text-center font-bold ${
                      idx === 0 ? "text-emerald-600" : "text-slate-400"
                    }`}
                  >
                    {idx + 1}
                  </span>
                  <span className="w-10 shrink-0 text-right font-semibold text-slate-800">
                    {it.score}
                  </span>
                  <span className="h-2 w-24 shrink-0 overflow-hidden rounded-full bg-slate-200">
                    <span
                      className="block h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500"
                      style={{ width: `${Math.max(3, it.score)}%` }}
                    />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-slate-700">{it.title}</span>
                  <span className="shrink-0 text-slate-400">
                    {it.position_category}/{it.experience_level}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {jds.length === 0 ? (
          <p className="rounded-lg border border-dashed border-slate-200 bg-white/50 p-6 text-center text-xs text-slate-400">
            岗位库为空或后端未连接数据库。可在后端用 <code>POST /jds</code> 写入岗位。
          </p>
        ) : (
          <div className="space-y-3">
            {groupedJds.map(([cat, items]) => {
              const open = expandedCat === cat;
              return (
                <div
                  key={cat}
                  className="rounded-xl border border-white/60 bg-white/70 shadow-sm backdrop-blur"
                >
                  {/* 大类折叠头：只显示分类名 + 岗位数，点击展开完整岗位 */}
                  <button
                    onClick={() => {
                      setExpandedCat(open ? null : cat);
                      setExpandedJd(null); // 收起时清空已展开的 JD 详情
                    }}
                    className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition hover:bg-violet-50/50"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-xs font-bold text-white">
                      {cat.slice(0, 1)}
                    </span>
                    <span className="text-sm font-semibold text-slate-800">{cat}</span>
                    <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-600">
                      {items.length} 个岗位
                    </span>
                    <span className="ml-auto shrink-0 text-xs text-slate-400">
                      {open ? "收起 ▲" : "展开 ▼"}
                    </span>
                  </button>

                  {/* 展开后：该分类下的完整岗位卡片 */}
                  {open && (
                    <div className="grid gap-2 border-t border-slate-100 p-3 sm:grid-cols-2">
                      {items.map((jd) => {
                        const active = selectedJd?.id === jd.id;
                        const jdOpen = expandedJd === jd.id;
                        return (
                          <div
                            key={jd.id}
                            className={`rounded-lg border p-3 text-left shadow-sm backdrop-blur transition ${
                              active
                                ? "border-violet-400 bg-violet-50"
                                : "border-white/60 bg-white/70"
                            }`}
                          >
                            {/* 标题行：点击展开/收起 JD 详情 */}
                            <button
                              onClick={() => setExpandedJd(jdOpen ? null : jd.id)}
                              className="w-full text-left"
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-slate-800">{jd.title}</span>
                                {jd.source === "user" && (
                                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                                    我的
                                  </span>
                                )}
                                <span className="ml-auto shrink-0 text-xs text-slate-400">
                                  {jdOpen ? "收起 ▲" : "JD 详情 ▼"}
                                </span>
                              </div>
                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                <Tag text={jd.position_category} />
                                <Tag text={jd.experience_level} />
                              </div>
                            </button>

                            {/* JD 完整介绍（职责 + 任职要求） */}
                            {jdOpen && (
                              <div className="mt-2 space-y-2">
                                <p className="max-h-56 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-2.5 text-xs leading-relaxed text-slate-600">
                                  {jd.content}
                                </p>
                                {jd.skills && jd.skills.length > 0 && (
                                  <div className="flex flex-wrap gap-1">
                                    {jd.skills.map((s) => (
                                      <span
                                        key={s}
                                        className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] text-violet-600"
                                      >
                                        {s}
                                      </span>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}

                            <button
                              onClick={() => void useJd(jd)}
                              disabled={optLoading}
                              className="mt-2.5 w-full rounded-md bg-gradient-to-r from-indigo-500 to-violet-500 py-1.5 text-xs font-medium text-white transition hover:opacity-90 disabled:opacity-60"
                            >
                              {optLoading && active ? "分析中…" : "用此岗位优化"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* 优化结果 */}
      {result && (
        <section ref={resultRef} className="space-y-6">
          <div className="rounded-xl bg-gradient-to-br from-indigo-500 via-violet-500 to-purple-500 p-6 text-center shadow-lg">
            <p className="text-sm text-indigo-100">简历与岗位匹配度得分</p>
            <p className="mt-1 text-6xl font-extrabold text-white">{result.score}</p>
            <div className="mt-3 flex items-center justify-center gap-2">
              {result.cache_hit ? (
                <span className="rounded-full bg-amber-300/90 px-3 py-1 text-xs font-semibold text-amber-900">
                  ⚡ 缓存命中（命中 {result.cache_source} 层）
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

          {/* 多维度评分 + 关键词命中高亮（与快速优化页同源） */}
          {result.breakdown?.dimensions?.length ? (
            <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-lg font-semibold text-slate-900">多维度评分明细</h2>
                <span className="text-xs text-slate-400">规则计算 · 可解释可改进</span>
              </div>
              <div className="space-y-4">
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
          ) : null}

          <ResultBlock title="分析报告" field="analysis" content={result.analysis} copied={copied} onCopy={handleCopy} />
          <ResultBlock title="优化建议" field="rewrite" content={result.rewrite} copied={copied} onCopy={handleCopy} />
        </section>
      )}
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number | string; tone: string }) {
  const tones: Record<string, string> = {
    emerald: "text-emerald-600",
    rose: "text-rose-600",
    slate: "text-slate-700",
    violet: "text-violet-600",
    amber: "text-amber-600",
  };
  return (
    <div className="rounded-lg bg-white/80 p-3 text-center shadow-sm">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`mt-1 text-2xl font-extrabold ${tones[tone] ?? "text-slate-700"}`}>{value}</p>
    </div>
  );
}

function Tag({ text }: { text: string }) {
  return (
    <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-600">
      {text}
    </span>
  );
}

function SelectField({
  label,
  value,
  onChange,
  options,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  placeholder: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-slate-700">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-indigo-200/70 bg-white/70 p-2.5 text-sm shadow-sm backdrop-blur outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200"
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </div>
  );
}

function ResultBlock({
  title,
  field,
  content,
  copied,
  onCopy,
}: {
  title: string;
  field: "analysis" | "rewrite";
  content: string;
  copied: "analysis" | "rewrite" | null;
  onCopy: (f: "analysis" | "rewrite") => void;
}) {
  return (
    <div className="rounded-xl border border-white/60 bg-white/70 p-6 shadow-sm backdrop-blur">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-lg font-semibold text-slate-900">{title}</h3>
        <button
          onClick={() => onCopy(field)}
          className="rounded-md border border-slate-300 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-100"
        >
          {copied === field ? "已复制 ✓" : "复制"}
        </button>
      </div>
      <Markdown>{content}</Markdown>
    </div>
  );
}
