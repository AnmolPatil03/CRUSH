import { useRef, useState } from "react";
import { api, fmtBytes, follow, type JobEvent, type Report, type Restored } from "../api";
import { Bar, Btn, Panel, Scramble, Segmented } from "./ui";

type Op = "compress" | "decompress";
type Item = { file: File; op: Op; state: "queued" | "running" | "done" | "error"; pct: number; log: JobEvent[];
  report?: Report; restored?: Restored; error?: string };

const ACCEPT: Record<Op, RegExp> = { compress: /\.(txt|docx|pdf)$/i, decompress: /\.crush$/i };

export function Compress({ onResult }: { onResult: (r: Report) => void }) {
  const [op, setOp] = useState<Op>("compress");
  const [items, setItems] = useState<Item[]>([]);
  const [mode, setMode] = useState<"lossless" | "optimised">("lossless");
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const queued = items.filter((i) => i.state === "queued" && i.op === op);

  const add = (list: FileList | null) => {
    if (!list) return;
    const files = [...list];
    const bad = files.filter((f) => !ACCEPT[op].test(f.name));
    setErr(bad.length ? `rejected: ${bad.map((f) => f.name).join(", ")} — ${op === "compress"
      ? "only .txt .docx .pdf" : "only .crush archives"}` : null);
    setItems((cur) => [...cur.filter((i) => i.state !== "done"),
      ...files.filter((f) => ACCEPT[op].test(f.name)).map((file) => ({ file, op, state: "queued" as const, pct: 0, log: [] }))]);
  };

  const patch = (file: File, p: Partial<Item> | ((i: Item) => Partial<Item>)) =>
    setItems((cur) => cur.map((i) => (i.file === file ? { ...i, ...(typeof p === "function" ? p(i) : p) } : i)));

  const run = async () => {
    const todo = queued;
    if (!todo.length) return;
    setBusy(true);
    setErr(null);
    try {
      const files = todo.map((i) => i.file);
      const { jobs } = op === "compress" ? await api.compress(files, mode) : await api.decompressUpload(files);
      let last: Report | null = null;
      await Promise.all(jobs.map(async (job, k) => {
        const file = todo[k].file;
        patch(file, { state: "running" });
        const done = await follow(job.id, (ev) => patch(file, (i) => ({
          log: [...i.log, ev].slice(-40),
          pct: ev.stage === "stream" && ev.total ? Math.max(i.pct, 10 + 75 * (ev.done! / ev.total))
            : ev.stage === "verify" ? 90 : ev.stage === "parse" ? Math.max(i.pct, 6) : i.pct,
        })));
        if (done.state !== "done") {
          patch(file, { state: "error", error: done.error ?? "failed" });
        } else if (op === "compress") {
          patch(file, { state: "done", pct: 100, report: done.result as Report });
          last = done.result as Report;
        } else {
          patch(file, { state: "done", pct: 100, restored: done.result as Restored });
        }
      }));
      // open the Result screen once, after the whole batch has finished
      if (last) setTimeout(() => onResult(last!), 450);
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const switchOp = (o: Op) => {
    if (busy) return;
    setOp(o);
    setErr(null);
  };

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
      <div className="space-y-6">
        <div
          className={`scan grid-bg flex min-h-64 cursor-pointer flex-col items-center justify-center border border-dashed p-8 text-center transition-colors ${drag ? "drift border-cyan bg-cyan/5" : "border-indigo/50 hover:border-cyan"}`}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
          onClick={() => input.current?.click()}
          role="button" tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        >
          <div className="font-mono text-4xl font-bold tracking-tight md:text-5xl">
            DROP<span className="text-pop">_</span><Scramble text={op === "compress" ? "FILES" : "ARCHIVES"} />
          </div>
          <p className="mt-3 font-mono text-xs uppercase tracking-[0.25em] text-dim">
            {op === "compress" ? ".txt · .docx · .pdf" : ".crush archives"} — multiple allowed — or click
          </p>
          <input ref={input} type="file" multiple accept={op === "compress" ? ".txt,.docx,.pdf" : ".crush"} className="hidden"
            onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
        </div>
        {err && <div className="rise border border-pop px-4 py-2 font-mono text-xs text-pop">! {err}</div>}

        <Panel title={`queue [${items.length}]`} right={items.length > 0 &&
          <button className="font-mono text-[10px] uppercase tracking-widest text-dim hover:text-pop" disabled={busy}
            onClick={() => setItems([])}>clear</button>}>
          {items.length === 0 ? <p className="font-mono text-sm text-dim cursor">awaiting input</p> : (
            <ul className="divide-y divide-line">
              {items.map((it, idx) => (
                <li key={it.op + it.file.name + it.file.size + it.file.lastModified} className="rise"
                  style={{ animationDelay: `${Math.min(idx, 8) * 45}ms` }}>
                  <div className={`-mx-2 px-2 py-3 ${it.state === "done" ? "flash" : ""}`}>
                  <div className="flex items-baseline justify-between gap-4 font-mono text-sm">
                    <span className="truncate">
                      <span className={`mr-2 text-[10px] uppercase tracking-widest ${it.op === "compress" ? "text-cyan" : "text-indigo"}`}>
                        {it.op === "compress" ? "▲ cmp" : "▼ dec"}
                      </span>
                      {it.file.name}
                    </span>
                    <span className="shrink-0 text-xs text-dim">
                      {fmtBytes(it.file.size)}
                      {it.report && <> → <span className="text-cyan">{fmtBytes(it.report.archive_size)}</span>
                        {" "}(<span className="text-pop">-{it.report.saved_pct.toFixed(1)}%</span>)</>}
                      {it.restored && <> → <span className="text-cyan">
                        {fmtBytes(it.restored.files.reduce((a, f) => a + f.size, 0))}</span></>}
                    </span>
                  </div>
                  <div className="mt-2"><Bar value={it.pct} active={it.state === "running"} /></div>
                  <div className="mt-1 flex justify-between font-mono text-[10px] uppercase tracking-widest text-dim">
                    <span className={it.state === "error" ? "text-pop" : it.state === "done" ? "text-cyan" : ""}>
                      {it.state === "error" ? `error: ${it.error}` : it.state === "done" && it.restored ? "done · sha-256 verified" : it.state}
                    </span>
                    {it.report && <button className="hover:text-cyan" onClick={() => onResult(it.report!)}>view result →</button>}
                  </div>
                  {it.restored && (
                    <div className="mt-2 flex flex-wrap gap-2">
                      {it.restored.files.map((f, k) => (
                        <a key={f.name} href={api.downloadUrl("decompressed", f.name)} className="rise"
                          style={{ animationDelay: `${120 + k * 60}ms` }}>
                          <Btn kind="ghost">↓ {f.name} · {fmtBytes(f.size)}</Btn>
                        </a>
                      ))}
                    </div>
                  )}
                  {it.state === "running" && (
                    <div className="mt-2 max-h-28 overflow-auto font-mono text-[11px] leading-5 text-dim">
                      {it.log.filter((e) => e.stage === "stream" && e.stream).map((e, k) => (
                        <div key={k} className="slide-in"><span className="text-cyan">✓</span> {e.stream?.padEnd(12, " ")} {e.codec}
                          {" "}{fmtBytes(e.raw ?? 0)} → {fmtBytes(e.packed ?? 0)}</div>
                      ))}
                      {it.log.length > 0 && <div className="cursor">{it.log[it.log.length - 1].stage}</div>}
                    </div>
                  )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="space-y-6">
        <Panel title="operation">
          <Segmented options={["compress", "decompress"] as const} value={op} onChange={switchOp} disabled={busy}
            tone={(o) => (o === "compress" ? "bg-cyan" : "bg-indigo")} />
          <p key={op} className="rise mt-3 font-mono text-xs leading-5 text-dim">
            {op === "compress"
              ? "Pack .txt / .docx / .pdf files into .crush archives."
              : "Restore .crush archives to the original files. Every file is checked against its SHA-256 before you get it."}
          </p>
        </Panel>

        {/* the mode panel folds away (height + fade) when decompressing */}
        <div className={`grid transition-[grid-template-rows,opacity,margin] duration-300 ${op === "compress" ? "grid-rows-[1fr] opacity-100" : "-mt-6 grid-rows-[0fr] opacity-0"}`}
          style={{ transitionTimingFunction: "var(--ease)" }} aria-hidden={op !== "compress"}>
          <div className="overflow-hidden" inert={op !== "compress"}>
            <Panel title="mode">
              <Segmented options={["lossless", "optimised"] as const} value={mode} onChange={setMode}
                tone={(m) => (m === "lossless" ? "bg-cyan" : "bg-pop")} />
              {mode === "lossless" ? (
                <p className="rise mt-3 font-mono text-xs leading-5 text-dim">Byte-for-byte identical output, verified with SHA-256.</p>
              ) : (
                <div className="rise mt-3 border-l-2 border-pop pl-3 font-mono text-xs leading-5">
                  <p className="uppercase tracking-widest text-pop">! lossy</p>
                  <p className="mt-1 text-dim">Images are downsampled to ~200 dpi (photos) and re-encoded. The document
                    looks the same at its printed size but is <span className="text-fg">not byte-identical</span> to the upload.</p>
                </div>
              )}
            </Panel>
          </div>
        </div>

        <Btn onClick={run} disabled={busy || queued.length === 0} busy={busy}>
          {busy ? (op === "compress" ? "crushing…" : "restoring…") : `${op} ▸ ${queued.length} file(s)`}
        </Btn>
      </div>
    </div>
  );
}
