import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type Report } from "./api";
import { Bench } from "./components/Bench";
import { Compress } from "./components/Compress";
import { Result } from "./components/Result";
import { Vault } from "./components/Vault";

const TABS = ["compress", "result", "vault", "benchmarks"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [tab, setTab] = useState<Tab>("compress");
  const [report, setReport] = useState<Report | null>(null);
  const [health, setHealth] = useState<{ jxl: boolean } | null>(null);
  const [offline, setOffline] = useState(false);

  useEffect(() => { api.health().then(setHealth).catch(() => setOffline(true)); }, []);

  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const [line, setLine] = useState({ left: 0, width: 0 });
  useLayoutEffect(() => {
    const place = () => {
      const el = tabRefs.current[TABS.indexOf(tab)];
      if (el) setLine({ left: el.offsetLeft, width: el.offsetWidth });
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [tab]);

  return (
    <div className="min-h-full">
      <header className="scan sticky top-0 z-10 border-b border-line bg-ink/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4 px-4 py-4 md:px-8">
          <div className="flex items-baseline gap-4">
            <h1 className="glitch font-mono text-2xl font-bold tracking-tight" data-text="CRUSH//">CRUSH<span className="text-pop">//</span></h1>
            <span className="hidden font-mono text-[10px] uppercase tracking-[0.3em] text-dim sm:inline">context-mixing compressor</span>
          </div>
          <nav className="relative flex flex-wrap font-mono text-xs uppercase tracking-widest">
            {TABS.map((t, k) => (
              <button key={t} ref={(el) => { tabRefs.current[k] = el; }} onClick={() => setTab(t)} aria-current={tab === t}
                className={`px-3 py-2 uppercase transition-colors duration-200 ${tab === t ? "text-cyan" : "text-dim hover:text-fg"}`}>
                <span className="text-indigo">0{k + 1}</span> {t}
              </button>
            ))}
            <span aria-hidden className="absolute bottom-0 h-0.5 bg-cyan transition-[left,width] duration-300"
              style={{ left: line.left, width: line.width, transitionTimingFunction: "var(--ease)" }} />
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-8 md:px-8">
        {offline && <div className="mb-6 border border-pop px-4 py-2 font-mono text-xs text-pop">! backend unreachable — run `crush serve`</div>}
        {/* kept mounted so the queue survives tab switches */}
        <div hidden={tab !== "compress"} className={tab === "compress" ? "rise" : ""}>
          <Compress onResult={(r) => { setReport(r); setTab("result"); }} />
        </div>
        {tab !== "compress" && (
          <div key={tab} className="rise">
            {tab === "result" && <Result report={report} />}
            {tab === "vault" && <Vault />}
            {tab === "benchmarks" && <Bench />}
          </div>
        )}
      </main>

      <footer className="mx-auto flex max-w-7xl flex-wrap justify-between gap-2 border-t border-line px-4 py-4 font-mono text-[10px] uppercase tracking-widest text-dim md:px-8">
        <span>lossless by default · sha-256 verified</span>
        <span>jpeg xl tools: {health ? (health.jxl ? <span className="text-cyan">found</span> : <span className="text-pop">missing — jpegs stored</span>) : "…"}</span>
      </footer>
    </div>
  );
}
