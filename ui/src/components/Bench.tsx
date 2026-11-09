import { useState } from "react";
import { api, fmtBytes, follow } from "../api";
import { Btn, CountUp, Panel } from "./ui";

type Codec = { size: number; saved_pct: number; bits_per_byte: number; enc_MBps: number; dec_MBps: number };
type Summary = { files: number; bytes: number; codecs: Record<string, Codec> };
type BenchResult = {
  formats: Record<string, { summary: Summary; peak_rss_mb: number }>;
  overall?: Summary;
  ablation?: { step: string; size: number; bits_per_char: number }[];
  images?: { file: string; w: number; h: number; c: number; raw: number; cm: number; pyramid: number | null; jxl?: number | null }[];
};

function Chart({ s, delay = 0 }: { s: Summary; delay?: number }) {
  const rows = Object.entries(s.codecs).sort((a, b) => b[1].saved_pct - a[1].saved_pct);
  return (
    <div className="space-y-2">
      {rows.map(([name, c], k) => (
        <div key={name} className="grid grid-cols-[80px_1fr_60px] items-center gap-3 font-mono text-xs">
          <span className={name === "crush" ? "text-pop" : "text-dim"}>{name}</span>
          <div className="h-4 bg-ink"><div className={`grow-in h-full ${name === "crush" ? "bg-pop" : "bg-indigo/50"}`}
            style={{ width: `${Math.max(0, c.saved_pct)}%`, animationDelay: `${delay + k * 80}ms` }} /></div>
          <span className="text-right tabular-nums">
            <CountUp to={c.saved_pct} format={(v) => `${v.toFixed(1)}%`} delay={delay + k * 80} />
          </span>
        </div>
      ))}
    </div>
  );
}

function Table({ s }: { s: Summary }) {
  return (
    <table className="mt-4 w-full font-mono text-xs">
      <thead className="text-dim"><tr className="text-right"><th className="text-left">codec</th><th>size</th><th>saved</th>
        <th>bits/B</th><th>enc MB/s</th><th>dec MB/s</th></tr></thead>
      <tbody>
        {Object.entries(s.codecs).sort((a, b) => a[1].size - b[1].size).map(([n, c]) => (
          <tr key={n} className={`border-t border-line text-right ${n === "crush" ? "text-pop" : ""}`}>
            <td className="py-1 text-left">{n}</td><td>{fmtBytes(c.size)}</td><td>{c.saved_pct.toFixed(1)}%</td>
            <td>{c.bits_per_byte.toFixed(3)}</td><td>{c.enc_MBps.toFixed(2)}</td><td>{c.dec_MBps.toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Bench() {
  const [folder, setFolder] = useState("corpus");
  const [ablation, setAblation] = useState(true);
  const [log, setLog] = useState<string[]>([]);
  const [res, setRes] = useState<BenchResult | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    setBusy(true);
    setLog([]);
    try {
      const job = await api.bench(folder, ablation);
      const done = await follow(job.id, (e) => e.message && setLog((l) => [...l, e.message!]));
      if (done.state === "done") setRes(done.result as BenchResult);
      else setLog((l) => [...l, `error: ${done.error}`]);
    } catch (e) {
      setLog((l) => [...l, `error: ${(e as Error).message}`]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <Panel title="benchmark config">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex-1 font-mono text-xs uppercase tracking-widest text-dim">folder (absolute, or relative to the project)
            <input value={folder} onChange={(e) => setFolder(e.target.value)}
              className="mt-2 block w-full border border-line bg-ink px-3 py-2 font-mono text-sm normal-case tracking-normal text-fg outline-none focus:border-cyan" />
          </label>
          <label className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest text-dim">
            <input type="checkbox" checked={ablation} onChange={(e) => setAblation(e.target.checked)} className="accent-[#2ec4d6]" />
            ablation
          </label>
          <Btn onClick={run} disabled={busy} busy={busy}>{busy ? "running…" : "run ▸"}</Btn>
        </div>
        {log.length > 0 && (
          <div className="mt-4 font-mono text-xs text-dim">{log.map((l, k) => <div key={k} className="slide-in">» {l}</div>)}
            {busy && <div className="cursor">working</div>}</div>
        )}
      </Panel>
      {res && (
        <>
          <div className="grid gap-6 lg:grid-cols-2">
            {Object.entries(res.formats).map(([fmt, f], k) => (
              <Panel key={fmt} className="rise" style={{ animationDelay: `${k * 90}ms` }} title={`${fmt} · ${f.summary.files} files · ${fmtBytes(f.summary.bytes)}`}
                right={<span className="font-mono text-[10px] text-dim">peak {f.peak_rss_mb.toFixed(0)} MiB</span>}>
                <Chart s={f.summary} delay={150 + k * 90} />
                <Table s={f.summary} />
              </Panel>
            ))}
          </div>
          {res.ablation && (
            <Panel title="ablation · text" className="rise" style={{ animationDelay: "300ms" }}>
              <table className="w-full font-mono text-xs">
                <tbody>
                  {res.ablation.map((a, k) => (
                    <tr key={a.step} className="rise border-t border-line first:border-0"
                      style={{ animationDelay: `${380 + k * 60}ms` }}>
                      <td className="py-1.5">{a.step}</td><td className="text-right">{a.size.toLocaleString()} B</td>
                      <td className="w-28 text-right text-cyan">{a.bits_per_char.toFixed(3)} bpc</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
          )}
          {res.images && res.images.length > 0 && (
            <Panel title="image codecs · lossless pixels" className="rise" style={{ animationDelay: "400ms" }}>
              <table className="w-full font-mono text-xs">
                <thead className="text-dim"><tr className="text-right"><th className="text-left">image</th><th>raw</th><th>CM</th><th>pyramid</th><th>JPEG XL</th></tr></thead>
                <tbody>
                  {res.images.map((im, k) => {
                    const best = Math.min(im.cm, im.pyramid ?? Infinity, im.jxl ?? Infinity);
                    const cell = (v: number | null | undefined) =>
                      <td className={v === best ? "text-cyan" : ""}>{v ? fmtBytes(v) : "—"}</td>;
                    return (
                      <tr key={k} className="border-t border-line text-right">
                        <td className="py-1 text-left">{im.file} <span className="text-dim">{im.w}×{im.h}×{im.c}</span></td>
                        <td>{fmtBytes(im.raw)}</td>{cell(im.cm)}{cell(im.pyramid)}{cell(im.jxl)}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}
