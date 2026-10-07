// 历史记录相关的共享类型与工具函数

export interface HistoryItem {
  score: number;
  analysis: string;
  rewrite: string;
  messages: string[];
  id: string;
  createdAt: string;
  jdExcerpt: string;
}

export const HISTORY_KEY = "resume-optimizer-history";
export const HISTORY_LIMIT = 10;

export function excerpt(text: string, max = 40): string {
  const firstLine = text.trim().split("\n")[0] ?? "";
  return firstLine.length > max ? `${firstLine.slice(0, max)}…` : firstLine;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function scoreColor(score: number): string {
  return score >= 80
    ? "text-emerald-600"
    : score >= 60
      ? "text-amber-600"
      : "text-rose-600";
}
