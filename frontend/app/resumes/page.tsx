"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Brand } from "../brand";
import { ApiError, apiGet, authFetch, getToken } from "../api";
import { formatDate } from "../history-utils";

interface ResumeItem {
  id: number;
  name: string | null;
  content: string;
  sections?: Record<string, unknown> | null;
  position_category?: string | null;
  experience_level?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface BasicInfo {
  name: string;
  phone: string;
  email: string;
  target: string;
}

// 结构化简历字段（对应后端 SavedResume.sections）
interface Sections {
  basic: BasicInfo;
  education: string;
  work: string;
  projects: string;
  skills: string;
  certificates: string;
  summary: string;
}

const EMPTY_SECTIONS: Sections = {
  basic: { name: "", phone: "", email: "", target: "" },
  education: "",
  work: "",
  projects: "",
  skills: "",
  certificates: "",
  summary: "",
};

const MAX_RESUMES = 3;

// 把结构化字段渲染成简历正文（技术岗常见写法）
function composeContent(s: Sections): string {
  const lines: string[] = [];
  const b = s.basic || ({} as BasicInfo);
  if (b.name) lines.push(`姓名：${b.name}`);
  if (b.target) lines.push(`求职意向：${b.target}`);
  if (b.phone) lines.push(`电话：${b.phone}`);
  if (b.email) lines.push(`邮箱：${b.email}`);
  lines.push("");

  if (s.education.trim()) {
    lines.push("【教育背景】", s.education.trim(), "");
  }
  if (s.work.trim()) {
    lines.push("【工作经历】", s.work.trim(), "");
  }
  if (s.projects.trim()) {
    lines.push("【项目经历】", s.projects.trim(), "");
  }
  if (s.skills.trim()) {
    lines.push("【专业技能】", s.skills.trim(), "");
  }
  if (s.certificates.trim()) {
    lines.push("【证书 / 荣誉】", s.certificates.trim(), "");
  }
  if (s.summary.trim()) {
    lines.push("【自我评价】", s.summary.trim(), "");
  }
  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

function labelCls() {
  return "mb-1 block text-sm font-medium text-slate-700";
}
function inputCls() {
  return "w-full rounded-lg border border-indigo-200/70 bg-white/70 p-2.5 text-sm shadow-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200";
}

export default function ResumesPage() {
  const router = useRouter();
  const [resumes, setResumes] = useState<ResumeItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<ResumeItem | "new" | null>(null);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");
  const [sections, setSections] = useState<Sections>(EMPTY_SECTIONS);
  const [structured, setStructured] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notify, setNotify] = useState("");

  const load = useCallback(async () => {
    try {
      const data = await apiGet<ResumeItem[]>("/resumes");
      setResumes(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/");
        return;
      }
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/");
      return;
    }
    void load();
  }, [load, router]);

  const openNew = () => {
    setEditing("new");
    setName("");
    setContent("");
    setSections(EMPTY_SECTIONS);
    setStructured(true);
    setError("");
    setNotify("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const openEdit = (item: ResumeItem) => {
    setEditing(item);
    setName(item.name ?? "");
    setContent(item.content);
    const raw = item.sections as Partial<Sections> | null | undefined;
    if (raw && raw.basic) {
      setSections({
        basic: { ...EMPTY_SECTIONS.basic, ...(raw.basic as Partial<BasicInfo>) },
        education: raw.education ?? "",
        work: raw.work ?? "",
        projects: raw.projects ?? "",
        skills: raw.skills ?? "",
        certificates: raw.certificates ?? "",
        summary: raw.summary ?? "",
      });
      setStructured(true);
    } else {
      setSections(EMPTY_SECTIONS);
      setStructured(false);
    }
    setError("");
    setNotify("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const updateBasic = (k: keyof BasicInfo, v: string) =>
    setSections((s) => ({ ...s, basic: { ...s.basic, [k]: v } }));
  const updateField = (k: Exclude<keyof Sections, "basic">, v: string) =>
    setSections((s) => ({ ...s, [k]: v }));

  const save = async () => {
    let text = content.trim();
    const sec = structured ? sections : null;
    if (structured) {
      text = composeContent(sections);
      setContent(text);
    }
    if (!text) {
      setError("简历内容不能为空（请至少填写一个栏目）");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const payload = {
        name: name.trim() || undefined,
        content: text,
        sections: sec || undefined,
      };
      if (editing && editing !== "new") {
        await authFetch(`/resumes/${editing.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        setNotify("已覆盖更新 ✓");
      } else {
        const res = await authFetch("/resumes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const created = (await res.json()) as ResumeItem;
        setNotify("已保存到简历库 ✓");
        setEditing(created);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (item: ResumeItem) => {
    if (!confirm(`确定删除简历「${item.name ?? "未命名简历"}」吗？`)) return;
    try {
      await authFetch(`/resumes/${item.id}`, { method: "DELETE" });
      if (editing && editing !== "new" && editing.id === item.id) {
        setEditing(null);
        setName("");
        setContent("");
        setNotify("");
      }
      await load();
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败，请重试");
    }
  };

  const charCount = (structured ? composeContent(sections) : content.trim()).length;

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Brand />
        <button
          onClick={() => router.push("/")}
          className="rounded-full border border-slate-200 bg-white/70 px-4 py-1.5 text-xs text-slate-500 shadow-sm transition hover:border-violet-300 hover:text-violet-600"
        >
          ← 返回首页
        </button>
      </div>

      <header className="mt-10 text-center">
        <h1 className="text-3xl font-extrabold tracking-tight text-slate-900">我的简历库</h1>
        <p className="mt-2 text-sm text-slate-500">
          最多保存 <span className="font-semibold text-violet-600">{MAX_RESUMES}</span> 份简历 ·
          优化时直接调用 · 改满意后覆盖更新
        </p>
      </header>

      {/* 编辑区 */}
      {editing && (
        <section className="glass-card mt-8 rounded-2xl p-6">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">
              {editing === "new" ? "新建简历" : `编辑「${editing.name ?? "未命名简历"}」`}
            </h2>
            <div className="flex items-center gap-3">
              {notify && <span className="text-xs text-emerald-600">{notify}</span>}
              <button
                onClick={() => {
                  setEditing(null);
                  setNotify("");
                }}
                className="rounded-md border border-slate-200 px-2.5 py-1 text-xs text-slate-400 transition hover:text-slate-600"
              >
                收起
              </button>
            </div>
          </div>

          <div className="space-y-4">
            <div>
              <label className={labelCls()}>简历名称</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="如：主简历 / 后端方向"
                className={inputCls()}
              />
            </div>

            {/* 填写方式切换 */}
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-400">填写方式：</span>
              {(["structured", "text"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setStructured(m === "structured")}
                  className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                    (m === "structured") === structured
                      ? "bg-violet-600 text-white"
                      : "bg-white/70 text-slate-500 hover:text-violet-600"
                  }`}
                >
                  {m === "structured" ? "结构化填写（推荐）" : "直接粘贴全文"}
                </button>
              ))}
            </div>

            {structured ? (
              <div className="space-y-4 rounded-xl border border-violet-100 bg-violet-50/40 p-4">
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <label className={labelCls()}>姓名</label>
                    <input
                      value={sections.basic.name}
                      onChange={(e) => updateBasic("name", e.target.value)}
                      placeholder="张三"
                      className={inputCls()}
                    />
                  </div>
                  <div>
                    <label className={labelCls()}>求职意向</label>
                    <input
                      value={sections.basic.target}
                      onChange={(e) => updateBasic("target", e.target.value)}
                      placeholder="如：Python 后端开发工程师"
                      className={inputCls()}
                    />
                  </div>
                  <div>
                    <label className={labelCls()}>电话</label>
                    <input
                      value={sections.basic.phone}
                      onChange={(e) => updateBasic("phone", e.target.value)}
                      placeholder="138-0000-0000"
                      className={inputCls()}
                    />
                  </div>
                  <div>
                    <label className={labelCls()}>邮箱</label>
                    <input
                      value={sections.basic.email}
                      onChange={(e) => updateBasic("email", e.target.value)}
                      placeholder="you@example.com"
                      className={inputCls()}
                    />
                  </div>
                </div>

                <div>
                  <label className={labelCls()}>教育背景</label>
                  <textarea
                    rows={3}
                    value={sections.education}
                    onChange={(e) => updateField("education", e.target.value)}
                    placeholder={"例：\nXX大学 · 计算机科学与技术 · 本科 · 2019-2023\n主修课程：数据结构、操作系统、计算机网络；GPA 3.6/4.0"}
                    className={inputCls()}
                  />
                </div>

                <div>
                  <label className={labelCls()}>工作经历</label>
                  <textarea
                    rows={4}
                    value={sections.work}
                    onChange={(e) => updateField("work", e.target.value)}
                    placeholder={"例：\nXX科技 · Python 后端开发工程师 · 2023.07-至今\n- 负责订单系统重构，QPS 提升 3 倍\n- 使用 FastAPI/MySQL/Redis 构建高并发 API"}
                    className={inputCls()}
                  />
                </div>

                <div>
                  <label className={labelCls()}>项目经历</label>
                  <textarea
                    rows={4}
                    value={sections.projects}
                    onChange={(e) => updateField("projects", e.target.value)}
                    placeholder={"例：\nAI 简历优化平台 · 全栈开发 · 2025\n- 基于 FastAPI + LangGraph 实现多轮对话改写\n- 负责 RAG 语义检索与三级缓存设计"}
                    className={inputCls()}
                  />
                </div>

                <div>
                  <label className={labelCls()}>专业技能</label>
                  <textarea
                    rows={2}
                    value={sections.skills}
                    onChange={(e) => updateField("skills", e.target.value)}
                    placeholder="Python、FastAPI、MySQL、Redis、Docker、LangChain"
                    className={inputCls()}
                  />
                </div>

                <div>
                  <label className={labelCls()}>证书 / 荣誉（可选）</label>
                  <textarea
                    rows={2}
                    value={sections.certificates}
                    onChange={(e) => updateField("certificates", e.target.value)}
                    placeholder="CET-6、软考中级、ACM 区域赛银奖…"
                    className={inputCls()}
                  />
                </div>

                <div>
                  <label className={labelCls()}>自我评价（可选）</label>
                  <textarea
                    rows={2}
                    value={sections.summary}
                    onChange={(e) => updateField("summary", e.target.value)}
                    placeholder="一句话概括你的优势…"
                    className={inputCls()}
                  />
                </div>
              </div>
            ) : null}

            <div>
              <div className="mb-1 flex items-baseline justify-between">
                <label className={labelCls()}>
                  {structured ? "生成的简历正文（可微调）" : "简历内容"}
                </label>
                <span className="text-xs text-slate-400">{charCount} 字</span>
              </div>
              <textarea
                rows={12}
                value={structured ? composeContent(sections) : content}
                onChange={(e) => setContent(e.target.value)}
                placeholder={
                  structured
                    ? "在上方结构化表单填写后，这里会自动生成简历正文；也可以直接手动修改。"
                    : "粘贴或编写简历全文…"
                }
                readOnly={structured}
                className={`${inputCls()} ${structured ? "bg-slate-50 text-slate-600" : ""}`}
              />
            </div>

            {error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</p>
            )}

            <div className="flex flex-wrap items-center gap-3">
              <button
                onClick={() => void save()}
                disabled={saving}
                className="rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 px-5 py-2 text-sm font-semibold text-white shadow transition hover:from-indigo-600 hover:to-violet-600 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
              >
                {saving ? "保存中…" : editing === "new" ? "保存" : "覆盖更新"}
              </button>
              {editing !== "new" && (
                <button
                  onClick={() => void remove(editing as ResumeItem)}
                  className="rounded-lg border border-rose-200 px-4 py-2 text-sm text-rose-500 transition hover:bg-rose-50"
                >
                  删除
                </button>
              )}
              <span className="text-xs text-slate-400">
                {editing === "new"
                  ? `当前已保存 ${resumes.length}/${MAX_RESUMES} 份`
                  : "覆盖后，旧内容将被新版本替换"}
              </span>
            </div>
          </div>
        </section>
      )}

      {/* 三槽位卡片 */}
      <section className="mt-8 grid gap-4 sm:grid-cols-3">
        {resumes.map((item) => (
          <div key={item.id} className="glass-card flex flex-col rounded-2xl p-5">
            <div className="flex items-start justify-between gap-2">
              <h3 className="truncate font-semibold text-slate-900">{item.name ?? "未命名简历"}</h3>
              <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-600">
                已保存
              </span>
            </div>
            <p className="mt-2 line-clamp-4 flex-1 text-xs leading-relaxed text-slate-500">
              {item.content}
            </p>
            <div className="mt-3 flex items-center justify-between text-[11px] text-slate-400">
              <span>{item.updated_at ? formatDate(item.updated_at) : ""}</span>
              <span>{item.content.length} 字</span>
            </div>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => openEdit(item)}
                className="flex-1 rounded-md border border-violet-200 bg-white/70 py-1.5 text-xs text-violet-600 transition hover:bg-violet-50"
              >
                编辑
              </button>
              <button
                onClick={() => void remove(item)}
                className="rounded-md border border-rose-100 px-2.5 py-1.5 text-xs text-rose-400 transition hover:bg-rose-50"
              >
                删除
              </button>
            </div>
          </div>
        ))}

        {resumes.length < MAX_RESUMES && (
          <button
            onClick={openNew}
            className="flex min-h-[10rem] flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-violet-200 bg-white/40 text-violet-400 transition hover:border-violet-400 hover:bg-white/70 hover:text-violet-600"
          >
            <span className="text-3xl font-light">＋</span>
            <span className="text-sm font-medium">新建一份简历</span>
            <span className="text-[11px]">还可保存 {MAX_RESUMES - resumes.length} 份</span>
          </button>
        )}
      </section>

      {loading && <p className="mt-10 text-center text-sm text-slate-400">正在加载简历库…</p>}
      {!loading && resumes.length === 0 && !editing && (
        <p className="mt-10 text-center text-sm text-slate-400">
          还没有保存的简历，点「新建一份简历」开始，或在首页优化后一键保存。
        </p>
      )}
    </main>
  );
}
