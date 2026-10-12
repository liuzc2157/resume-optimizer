// 轻量 API 客户端：统一携带 JWT、处理 401/429，供所有业务页面复用。
"use client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "resume-optimizer-token";
const USER_KEY = "resume-optimizer-user";

export interface UserProfile {
  id: number;
  username: string;
  email?: string | null;
  plan: string;
  daily_quota: number;
}

export interface QuotaInfo {
  daily: number;
  remaining: number;
  unlimited: boolean;
}

export interface MeResponse {
  profile: UserProfile;
  quota: QuotaInfo;
  usage: Record<string, unknown>;
}

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 隐私模式等场景可能写入失败，忽略即可
  }
}

function safeRemove(key: string) {
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

export function getToken(): string | null {
  return safeGet(TOKEN_KEY);
}

export function getStoredUser(): UserProfile | null {
  const raw = safeGet(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as UserProfile;
  } catch {
    return null;
  }
}

export function setAuth(token: string, user: UserProfile) {
  safeSet(TOKEN_KEY, token);
  safeSet(USER_KEY, JSON.stringify(user));
}

export function clearAuth() {
  safeRemove(TOKEN_KEY);
  safeRemove(USER_KEY);
}

// 登录态失效回调：任意接口返回 401 时触发，页面据此回到登录界面
type Listener = () => void;
const listeners = new Set<Listener>();

export function onUnauthorized(fn: Listener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function emitUnauthorized() {
  clearAuth();
  listeners.forEach((fn) => fn());
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function readDetail(res: Response): Promise<string> {
  try {
    const body = await res.clone().json();
    if (body && typeof body.detail === "string" && body.detail) return body.detail;
    if (Array.isArray(body?.detail) && body.detail.length > 0) {
      // Pydantic 校验错误：取第一条消息
      const first = body.detail[0];
      if (first && typeof first.msg === "string") return first.msg;
    }
  } catch {
    // 非 JSON 响应
  }
  return "";
}

export async function authFetch(
  path: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = getToken();
  const headers = new Headers(options.headers ?? {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (
    options.body &&
    !(options.body instanceof FormData) &&
    !headers.has("Content-Type")
  ) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...options, headers });
  } catch {
    throw new ApiError(0, "无法连接后端服务，请确认后端已启动");
  }

  if (res.status === 401) {
    emitUnauthorized();
    throw new ApiError(401, (await readDetail(res)) || "登录已过期，请重新登录");
  }
  if (res.status === 429) {
    throw new ApiError(429, (await readDetail(res)) || "请求过于频繁，请稍后再试");
  }
  if (!res.ok) {
    throw new ApiError(res.status, (await readDetail(res)) || `请求失败（${res.status}）`);
  }
  return res;
}

export async function apiGet<T>(path: string): Promise<T> {
  const res = await authFetch(path);
  return (await res.json()) as T;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await authFetch(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await res.json()) as T;
}