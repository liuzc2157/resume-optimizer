"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ApiError,
  MeResponse,
  QuotaInfo,
  UserProfile,
  apiGet,
  authFetch,
  clearAuth,
  getStoredUser,
  getToken,
  onUnauthorized,
  setAuth,
} from "./api";
import { BrandMark } from "./brand";

export interface AuthState {
  ready: boolean;
  user: UserProfile | null;
  quota: QuotaInfo | null;
  refresh: () => Promise<void>;
  logout: () => void;
}

export function useAuth(): AuthState {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<UserProfile | null>(() => getStoredUser());
  const [quota, setQuota] = useState<QuotaInfo | null>(null);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setQuota(null);
      setReady(true);
      return;
    }
    try {
      const me = await apiGet<MeResponse>("/users/me");
      setUser(me.profile);
      setQuota(me.quota);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setUser(null);
        setQuota(null);
      }
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const off = onUnauthorized(() => {
      setUser(null);
      setQuota(null);
    });
    return off;
  }, [refresh]);

  const logout = useCallback(() => {
    clearAuth();
    setUser(null);
    setQuota(null);
  }, []);

  return { ready, user, quota, refresh, logout };
}

// 三档密码强度（仅注册时展示）
function passwordStrength(pwd: string) {
  if (!pwd) return null;
  let score = 0;
  if (pwd.length >= 8) score += 1;
  if (/[A-Za-z]/.test(pwd) && /\d/.test(pwd)) score += 1;
  if (/[^A-Za-z0-9]/.test(pwd)) score += 1;
  if (score >= 2) return { label: "较强", bar: "bg-emerald-500", text: "text-emerald-600" };
  if (score === 1) return { label: "一般", bar: "bg-amber-500", text: "text-amber-600" };
  return { label: "较弱", bar: "bg-rose-400", text: "text-rose-500" };
}

const SELL_POINTS = [
  {
    icon: "📊",
    title: "ATS 多维评分",
    desc: "关键词 / 经验 / 量化 / 结构，一眼看清短板",
  },
  {
    icon: "✍️",
    title: "AI 分析改写",
    desc: "贴合目标 JD，逐句给出可落地的修改",
  },
  {
    icon: "🎯",
    title: "岗位智能匹配",
    desc: "没想好投哪？先找到最匹配的岗位",
  },
];

// 登录 / 注册：左右分栏，两模式视觉与信息明显区分
export type AuthMode = "login" | "register" | "reset";

export function AuthScreen({ onSuccess }: { onSuccess: () => void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  // 登录：账号（用户名或邮箱）
  const [account, setAccount] = useState("");
  // 注册 / 找回
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sendingCode, setSendingCode] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [codeInfo, setCodeInfo] = useState("");
  const [cooldown, setCooldown] = useState(0);

  const switchMode = (next: AuthMode) => {
    setMode(next);
    setError("");
    setCodeInfo("");
    setCodeSent(false);
    setCode("");
    setCooldown(0);
  };

  // 发送验证码（注册 / 找回共用；冷却倒计时由前端本地计时）
  const sendCode = async () => {
    const target = mode === "register" ? email : email;
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(target)) {
      setError("请输入正确的邮箱地址");
      return;
    }
    setSendingCode(true);
    setError("");
    try {
      const res = await authFetch("/auth/send-code", {
        method: "POST",
        body: JSON.stringify({ email: target, purpose: mode === "reset" ? "reset" : "register" }),
      });
      const data = await res.json();
      setCodeSent(true);
      if (data.dev && data.dev_code) {
        setCode(data.dev_code);
        setCodeInfo("开发模式：验证码已自动填入（未配置邮件服务）");
      } else {
        setCodeInfo(`验证码已发送到 ${target}，10 分钟内有效`);
      }
      let left = Math.max(data.cooldown_seconds ?? 60, 60);
      setCooldown(left);
      const timer = setInterval(() => {
        left -= 1;
        setCooldown(Math.max(0, left));
        if (left <= 0) clearInterval(timer);
      }, 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送失败，请重试");
    } finally {
      setSendingCode(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "login") {
      if (!account.trim() || !password) {
        setError("请填写账号和密码");
        return;
      }
    } else {
      if (!username.trim()) {
        setError("请填写用户名");
        return;
      }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        setError("请输入正确的邮箱地址");
        return;
      }
      if (code.length < 6) {
        setError("请先获取并填写邮箱验证码");
        return;
      }
      if (password.length < 6) {
        setError("密码至少 6 位");
        return;
      }
      if (password !== confirmPwd) {
        setError("两次输入的密码不一致");
        return;
      }
      if (mode === "register" && !consent) {
        setError("请先勾选同意《隐私政策》");
        return;
      }
    }
    setBusy(true);
    setError("");
    try {
      const body =
        mode === "login"
          ? { account: account.trim(), password }
          : mode === "register"
          ? { username: username.trim(), email, code, password, consent: true }
          : { email, code, new_password: password };
      const res = await authFetch(
        mode === "login"
          ? "/auth/login"
          : mode === "register"
          ? "/auth/register"
          : "/auth/reset-password",
        { method: "POST", body: JSON.stringify(body) }
      );
      const data = await res.json();
      setAuth(data.token, data.user);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  const strength = passwordStrength(password);

  const inputCls =
    "w-full rounded-lg border border-indigo-200/70 bg-white/70 p-2.5 text-sm shadow-sm outline-none transition focus:border-violet-500 focus:ring-2 focus:ring-violet-200";

  return (
    <div className="mx-auto mt-10 w-full max-w-3xl">
      <div className="glass-card grid overflow-hidden rounded-3xl lg:grid-cols-[1.05fr_1fr]">
        {/* 左侧品牌区：随模式切换文案 */}
        <div className="relative hidden flex-col justify-between overflow-hidden bg-brand-gradient p-8 text-white lg:flex">
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/10 blur-2xl" />
          <div className="pointer-events-none absolute -bottom-20 -left-10 h-64 w-64 rounded-full bg-white/10 blur-3xl" />
          <div className="relative">
            <BrandMark size={44} />
            <h2 className="mt-5 text-2xl font-extrabold leading-snug">
              {mode === "login" ? "欢迎回来 👋" : mode === "register" ? "开始你的简历焕新" : "找回密码 🔑"}
            </h2>
            <p className="mt-2 text-sm leading-relaxed text-white/85">
              {mode === "login"
                ? "回到你的简历，继续打磨细节，遇见更好的机会。"
                : mode === "register"
                ? "几分钟内，让简历从「能看」变成「能打」。"
                : "通过注册邮箱验证码重置密码，重新出发。"}
            </p>
          </div>
          <ul className="relative mt-8 space-y-3">
            {SELL_POINTS.map((p) => (
              <li
                key={p.title}
                className="flex items-start gap-3 rounded-xl bg-white/10 px-3.5 py-2.5 backdrop-blur"
              >
                <span className="text-lg leading-none">{p.icon}</span>
                <span>
                  <span className="block text-sm font-semibold">{p.title}</span>
                  <span className="block text-xs text-white/75">{p.desc}</span>
                </span>
              </li>
            ))}
          </ul>
          <p className="relative mt-8 text-[11px] text-white/60">
            免费版每日 10 次配额 · 数据仅用于优化 · 可随时注销删除
          </p>
        </div>

        {/* 右侧表单区 */}
        <div className="p-7 sm:p-9">
          <div className="mb-5 flex justify-center lg:hidden">
            <BrandMark size={44} />
          </div>
          <h2 className="text-xl font-bold text-slate-900">
            {mode === "login" ? "登录简遇" : mode === "register" ? "创建账号" : "重置密码"}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {mode === "login"
              ? "使用用户名或邮箱登录"
              : mode === "register"
              ? "邮箱验证码注册，一个邮箱一个账号"
              : "输入注册邮箱，获取验证码后设置新密码"}
          </p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            {mode === "login" ? (
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">用户名 / 邮箱</label>
                <input
                  value={account}
                  onChange={(e) => setAccount(e.target.value)}
                  placeholder="用户名或注册邮箱"
                  autoComplete="username"
                  className={inputCls}
                />
              </div>
            ) : (
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">用户名</label>
                <input
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="2-64 个字符"
                  autoComplete="username"
                  className={inputCls}
                />
              </div>
            )}

            {mode !== "login" && (
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">邮箱</label>
                <div className="flex gap-2">
                  <input
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    autoComplete="email"
                    className={inputCls}
                  />
                  <button
                    type="button"
                    onClick={() => void sendCode()}
                    disabled={sendingCode || cooldown > 0}
                    className="shrink-0 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-medium text-violet-600 transition hover:bg-violet-100 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {cooldown > 0 ? `${cooldown}s 后重发` : sendingCode ? "发送中…" : "发送验证码"}
                  </button>
                </div>
                {codeInfo && <p className="mt-1 text-[11px] text-emerald-600">{codeInfo}</p>}
              </div>
            )}

            {mode !== "login" && (
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">验证码</label>
                <input
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder="6 位数字验证码"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  className={inputCls}
                />
              </div>
            )}

            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">
                {mode === "reset" ? "新密码" : "密码"}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={mode === "register" || mode === "reset" ? "至少 6 位" : "请输入密码"}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                className={inputCls}
              />
              {(mode === "register" || mode === "reset") && strength && (
                <div className="mt-1.5 flex items-center gap-2">
                  <div className="flex h-1.5 flex-1 gap-1">
                    {[0, 1, 2].map((i) => (
                      <span
                        key={i}
                        className={`h-full flex-1 rounded-full transition-colors ${
                          i < (strength.label === "较强" ? 3 : strength.label === "一般" ? 2 : 1)
                            ? strength.bar
                            : "bg-slate-200"
                        }`}
                      />
                    ))}
                  </div>
                  <span className={`text-[10px] font-medium ${strength.text}`}>{strength.label}</span>
                </div>
              )}
            </div>

            {mode !== "login" && (
              <div>
                <input
                  type="password"
                  value={confirmPwd}
                  onChange={(e) => setConfirmPwd(e.target.value)}
                  placeholder="再次输入密码"
                  autoComplete="new-password"
                  className={inputCls}
                />
              </div>
            )}

            {mode === "register" && (
              <label className="flex items-start gap-2 text-xs leading-relaxed text-slate-500">
                <input
                  type="checkbox"
                  checked={consent}
                  onChange={(e) => setConsent(e.target.checked)}
                  className="mt-0.5"
                />
                <span>
                  我已阅读并同意《隐私政策》：简历内容属于个人信息，仅用于简历优化分析，
                  不会对外分享；我可以随时在「账号」里注销并彻底删除全部数据。
                </span>
              </label>
            )}

            {error && (
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</p>
            )}

            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-500 py-2.5 text-sm font-semibold text-white shadow transition hover:from-indigo-600 hover:to-violet-600 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300"
            >
              {busy
                ? "请稍候…"
                : mode === "login"
                ? "登录"
                : mode === "register"
                ? "注册并开始使用"
                : "重置密码并登录"}
            </button>
          </form>

          <p className="mt-5 text-center text-xs text-slate-400">
            {mode === "login" ? (
              <>
                还没有账号？{" "}
                <button
                  onClick={() => switchMode("register")}
                  className="font-medium text-violet-600 hover:underline"
                >
                  免费注册
                </button>
                <span className="mx-2 text-slate-300">|</span>
                <button
                  onClick={() => switchMode("reset")}
                  className="font-medium text-violet-600 hover:underline"
                >
                  忘记密码？
                </button>
              </>
            ) : (
              <>
                已有账号？{" "}
                <button
                  onClick={() => switchMode(mode === "register" ? "login" : "login")}
                  className="font-medium text-violet-600 hover:underline"
                >
                  去登录
                </button>
              </>
            )}
          </p>
        </div>
      </div>
      <p className="mt-4 text-center text-[11px] text-slate-400">
        免费版每日有调用配额；登录后在右上角可查看剩余次数，也可随时注销账号清除全部数据。
      </p>
    </div>
  );
}

// 右上角账号信息栏：用户名 + 剩余配额 + 退出 / 注销（删除数据）
export function UserChip({ auth }: { auth: AuthState }) {
  const [busy, setBusy] = useState(false);
  const router = useRouter();

  const { user, quota } = auth;
  if (!user) return null;

  const quotaText = quota
    ? quota.unlimited
      ? "不限次"
      : `今日剩余 ${quota.remaining}/${quota.daily}`
    : "";

  const handleDelete = async () => {
    if (!confirm("确定注销账号并删除全部数据吗？该操作不可恢复。")) return;
    setBusy(true);
    try {
      await authFetch("/users/me", { method: "DELETE" });
      clearAuth();
      auth.logout();
      alert("账号已注销，所有个人数据已清除");
    } catch (err) {
      alert(err instanceof Error ? err.message : "注销失败，请重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className="rounded-full border border-violet-200 bg-white/70 px-3 py-1 text-xs text-slate-600 shadow-sm backdrop-blur"
        title="点击刷新配额"
      >
        <button
          onClick={() => router.push("/resumes")}
          className="hover:text-violet-600"
          title="进入我的简历库"
        >
          {user.username}
        </button>
        {user.plan !== "free" && (
          <span className="ml-1 rounded bg-violet-100 px-1 text-[10px] font-semibold text-violet-600">
            {user.plan}
          </span>
        )}
        {quotaText && <span className="ml-1.5 text-slate-400">· {quotaText}</span>}
      </span>
      <button
        onClick={auth.logout}
        disabled={busy}
        className="rounded-full border border-slate-200 bg-white/70 px-3 py-1 text-xs text-slate-500 shadow-sm transition hover:border-rose-300 hover:text-rose-500"
      >
        退出
      </button>
      <button
        onClick={() => void handleDelete()}
        disabled={busy}
        className="rounded-full border border-rose-200 bg-white/70 px-3 py-1 text-xs text-rose-500 shadow-sm transition hover:bg-rose-50"
      >
        {busy ? "处理中…" : "注销并删除数据"}
      </button>
    </div>
  );
}