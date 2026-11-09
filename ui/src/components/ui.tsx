import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function Panel({ title, right, children, className = "", style }:
  { title: string; right?: ReactNode; children: ReactNode; className?: string; style?: CSSProperties }) {
  return (
    <section className={`border border-line bg-panel/80 ${className}`} style={style}>
      <header className="flex items-center justify-between border-b border-line px-4 py-2 font-mono text-xs uppercase tracking-widest text-dim">
        <span><span className="text-indigo">//</span> {title}</span>
        {right}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Btn({ children, onClick, kind = "solid", disabled, title, busy }:
  { children: ReactNode; onClick?: () => void; kind?: "solid" | "ghost" | "danger"; disabled?: boolean; title?: string; busy?: boolean }) {
  const base = "font-mono text-xs uppercase tracking-widest px-3 py-2 border transition-[background,color,border-color,transform] duration-150 active:translate-y-px disabled:pointer-events-none";
  const styles = {
    solid: "bg-pop text-ink border-pop hover:bg-ink hover:text-pop",
    ghost: "border-line text-fg hover:border-cyan hover:text-cyan",
    danger: "border-line text-dim hover:border-pop hover:text-pop",
  }[kind];
  return (
    <button className={`${base} ${styles} ${busy ? "busy-stripes" : "disabled:opacity-40"}`} onClick={onClick}
      disabled={disabled} title={title} aria-busy={busy}>
      {children}
    </button>
  );
}

/** Two-or-more option switch whose highlight slides to the selected option. */
export function Segmented<T extends string>({ options, value, onChange, tone, disabled }:
  { options: readonly T[]; value: T; onChange: (v: T) => void; tone: (v: T) => string; disabled?: boolean }) {
  const i = Math.max(0, options.indexOf(value));
  return (
    <div className="relative grid border border-line font-mono text-xs uppercase tracking-widest"
      style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}>
      <div aria-hidden className={`absolute inset-y-0 left-0 transition-[transform,background-color] duration-300 ${tone(value)}`}
        style={{ width: `${100 / options.length}%`, transform: `translateX(${i * 100}%)`, transitionTimingFunction: "var(--ease)" }} />
      {options.map((o) => (
        <button key={o} onClick={() => onChange(o)} aria-pressed={value === o} disabled={disabled}
          className={`relative px-3 py-3 uppercase transition-colors duration-300 ${value === o ? "text-ink" : "text-dim hover:text-fg"}`}>
          {o}
        </button>
      ))}
    </div>
  );
}

/** Animate a number from 0 to ``to`` (ease-out cubic). */
export function useCountUp(to: number, ms = 700, delay = 0): number {
  const [v, setV] = useState(reducedMotion() ? to : 0);
  useEffect(() => {
    if (reducedMotion()) { setV(to); return; }
    let raf = 0;
    const start = performance.now() + delay;
    const tick = (t: number) => {
      const k = Math.min(1, Math.max(0, (t - start) / ms));
      setV(to * (1 - Math.pow(1 - k, 3)));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to, ms, delay]);
  return v;
}

export function CountUp({ to, format, delay = 0 }: { to: number; format: (v: number) => string; delay?: number }) {
  return <>{format(useCountUp(to, 750, delay))}</>;
}

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&*/<>";

/** Terminal-style text change: characters resolve left to right from random glyphs. */
export function Scramble({ text, ms = 320 }: { text: string; ms?: number }) {
  const [shown, setShown] = useState(text);
  const first = useRef(true);
  useEffect(() => {
    if (first.current || reducedMotion()) { first.current = false; setShown(text); return; }
    let raf = 0;
    const start = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - start) / ms);
      const fixed = Math.floor(k * text.length);
      setShown(text.slice(0, fixed) + [...text.slice(fixed)].map(() => GLYPHS[(Math.random() * GLYPHS.length) | 0]).join(""));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text, ms]);
  return <>{shown}</>;
}

export function Stat({ label, value, accent, delay = 0 }: { label: string; value: ReactNode; accent?: boolean; delay?: number }) {
  return (
    <div className="rise border border-line p-4" style={{ animationDelay: `${delay}ms` }}>
      <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-dim">{label}</div>
      <div className={`mt-2 font-mono text-2xl font-bold tabular-nums md:text-3xl ${accent ? "text-pop" : ""}`}>{value}</div>
    </div>
  );
}

export function Bar({ value, max = 100, active }: { value: number; max?: number; active?: boolean }) {
  return (
    <div className="h-1.5 w-full bg-indigo/20">
      <div className={`h-full bg-cyan transition-[width] duration-500 ${active ? "shimmer" : ""}`}
        style={{ width: `${Math.min(100, (value / max) * 100)}%`, transitionTimingFunction: "var(--ease)" }} />
    </div>
  );
}
