import { api, fmtBytes, type Report } from "../api";
import { Btn, CountUp, Panel, Stat } from "./ui";

const GROUP: Record<string, string> = {
  TEXT: "text", XML: "xml", PDF_OPS: "pdf operators", PDF_OBJ: "pdf structure", FONT: "fonts", IMAGE: "images",
  BINARY: "binary", META: "container meta", DEFLATE_SIDE: "deflate rebuild", MANIFEST: "manifest",
};

function breakdown(r: Report) {
  const g = new Map<string, { raw: number; packed: number }>();
  for (const s of r.streams) {
    const key = s.codec === "STORE" && s.raw > 0 ? "stored" : GROUP[s.type] ?? s.type.toLowerCase();
    const cur = g.get(key) ?? { raw: 0, packed: 0 };
    g.set(key, { raw: cur.raw + s.raw, packed: cur.packed + s.packed });
  }
  return [...g.entries()].filter(([, v]) => v.raw > 0).sort((a, b) => b[1].raw - a[1].raw);
}

export function Result({ report }: { report: Report | null }) {
  if (!report) {
    return <Panel title="result"><p className="font-mono text-sm text-dim cursor">no result yet — compress something first</p></Panel>;
  }
  const rows = breakdown(report);
  const max = Math.max(...rows.map(([, v]) => v.raw), 1);
  const lossy = report.mode === "optimised";
  return (
    <div key={report.archive} className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="font-mono text-xs uppercase tracking-[0.25em] text-dim">archive</div>
          <h2 className="mt-1 break-all font-mono text-xl md:text-2xl">{report.archive}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {report.verified && (
            <span style={{ animationDelay: "800ms" }} className={`stamp border px-3 py-2 font-mono text-xs uppercase tracking-widest ${lossy ? "border-pop text-pop" : "border-cyan text-cyan"}`}
              title={report.files.map((f) => `${f.name}: ${f.sha256}`).join("\n")}>
              ■ sha-256 verified{lossy && " (optimised copy)"}
            </span>
          )}
          <a href={api.downloadUrl("compressed", report.archive)}><Btn>download .crush</Btn></a>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px md:grid-cols-5">
        <Stat label="original" value={fmtBytes(report.original_size)} />
        <Stat label="compressed" delay={60}
          value={<CountUp to={report.archive_size} format={(v) => fmtBytes(Math.round(v))} delay={60} />} />
        <Stat label="saved" accent delay={120} value={<CountUp to={report.saved_pct} format={(v) => `${v.toFixed(1)}%`} delay={120} />} />
        <Stat label="ratio" delay={180} value={<CountUp to={report.ratio} format={(v) => `${v.toFixed(2)}:1`} delay={180} />} />
        <Stat label={report.bits_per_char !== undefined ? "bits / char" : "time"} delay={240}
          value={report.bits_per_char !== undefined
            ? <CountUp to={report.bits_per_char} format={(v) => v.toFixed(3)} delay={240} />
            : `${report.seconds.toFixed(1)}s`} />
      </div>

      <Panel title="per-stream breakdown" right={<span className="font-mono text-[10px] text-dim">indigo = in · cyan = out</span>}>
        <div className="space-y-3">
          {rows.map(([name, v], k) => (
            <div key={name} style={{ animationDelay: `${250 + k * 70}ms` }}
              className="rise grid grid-cols-[120px_1fr_150px] items-center gap-3 font-mono text-xs md:grid-cols-[160px_1fr_190px]">
              <span className="uppercase tracking-widest text-dim">{name}</span>
              <div className="relative h-5 bg-ink">
                <div className="grow-in absolute inset-y-0 left-0 bg-indigo/35"
                  style={{ width: `${(v.raw / max) * 100}%`, animationDelay: `${300 + k * 70}ms` }} />
                <div className="grow-in absolute inset-y-1 left-0 bg-cyan"
                  style={{ width: `${(v.packed / max) * 100}%`, animationDelay: `${520 + k * 70}ms` }} />
              </div>
              <span className="text-right">{fmtBytes(v.raw)} → <span className="text-cyan">{fmtBytes(v.packed)}</span></span>
            </div>
          ))}
        </div>
        <details className="mt-4 font-mono text-xs text-dim">
          <summary className="cursor-pointer uppercase tracking-widest hover:text-fg">raw stream table</summary>
          <table className="mt-3 w-full">
            <tbody>
              {report.streams.map((s, k) => (
                <tr key={k} className="border-t border-line">
                  <td className="py-1">{s.type}</td><td>{s.codec}</td><td className="text-right">{s.raw.toLocaleString()}</td>
                  <td className="text-right">{s.packed.toLocaleString()}</td><td className="pl-3">{s.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(report.stats).length > 0 && (
            <p className="mt-3">{Object.entries(report.stats).map(([k, v]) => `${k}=${v}`).join(" · ")}</p>
          )}
        </details>
      </Panel>

      {report.optimised?.some((o) => o.images.length) && (
        <Panel title="optimised images">
          <table className="w-full font-mono text-xs">
            <thead className="text-dim"><tr><th className="text-left">image</th><th>kind</th><th>pixels</th><th>dpi</th><th>bytes</th><th>psnr</th></tr></thead>
            <tbody>
              {report.optimised.flatMap((o) => o.images).map((im, k) => (
                <tr key={k} className="border-t border-line text-center">
                  <td className="py-1 text-left">{im.image}</td><td>{im.kind}</td>
                  <td>{im.from.join("×")} → {im.to.join("×")}</td><td>{im.dpi_before} → {im.dpi_after}</td>
                  <td>{fmtBytes(im.bytes_before)} → {fmtBytes(im.bytes_after)}</td><td>{im.psnr_db} dB</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
      {report.notes.length > 0 && (
        <div className="space-y-1 font-mono text-xs text-dim">{report.notes.map((n) => <p key={n}>» {n}</p>)}</div>
      )}
    </div>
  );
}
