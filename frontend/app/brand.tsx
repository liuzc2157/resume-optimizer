"use client";

// 品牌标识：简遇 Briefly（简历 + 对勾标志）
export function BrandMark({ size = 36 }: { size?: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-xl bg-brand-gradient text-white shadow-lg shadow-violet-400/30"
      style={{ width: size, height: size }}
    >
      <svg
        width={Math.round(size * 0.56)}
        height={Math.round(size * 0.56)}
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden
      >
        <path
          d="M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinejoin="round"
        />
        <path
          d="M8 8h8M8 12h5M8 16h3"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="m13.5 13.5 2 2 3-3.5"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <BrandMark size={compact ? 30 : 36} />
      <div className="leading-tight">
        <div className="flex items-center gap-1.5">
          <span className="text-lg font-extrabold tracking-tight text-slate-900">简遇</span>
          <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-[9px] font-semibold tracking-wide text-violet-600">
            Briefly
          </span>
        </div>
        {!compact && (
          <p className="text-[10px] text-slate-400">简历焕新 · 遇见好机会</p>
        )}
      </div>
    </div>
  );
}