import { useEffect, useState } from "react";
import type { ReactNode } from "react";

export function useAsync<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    loader()
      .then((value) => {
        if (!alive) return;
        setData(value);
        setError(null);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading };
}

export type Tone = "ok" | "warn" | "err" | "info" | "neutral";

const TONE_CLASS: Record<Tone, string> = {
  ok: "pill-ok",
  warn: "pill-warn",
  err: "pill-err",
  info: "pill-info",
  neutral: "pill-neutral",
};

const TONE_GLYPH: Record<Tone, string> = {
  ok: "●",
  warn: "◐",
  err: "✕",
  info: "◆",
  neutral: "○",
};

export function StatusPill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`pill ${TONE_CLASS[tone]}`}>
      <span aria-hidden="true">{TONE_GLYPH[tone]}</span>
      {children}
    </span>
  );
}

export function Card({
  title,
  actions,
  children,
  bodyless,
}: {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  bodyless?: boolean;
}) {
  return (
    <section className="card">
      {(title || actions) && (
        <header className="card-head">
          <h2>{title}</h2>
          {actions}
        </header>
      )}
      {bodyless ? children : <div className="card-body">{children}</div>}
    </section>
  );
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="error-banner" role="alert">
      {message}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading({ label = "Загрузка…" }: { label?: string }) {
  return <div className="empty">{label}</div>;
}

export function formatEpoch(seconds: number | null): string {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString();
}

export function formatIso(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
