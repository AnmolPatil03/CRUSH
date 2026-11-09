import { useCallback, useEffect, useState } from "react";
import { api, fmtBytes, follow, type VaultRow } from "../api";
import { Btn, Panel } from "./ui";

function List({ kind, refresh, onChange }: { kind: "compressed" | "decompressed"; refresh: number; onChange: () => void }) {
  const [rows, setRows] = useState<VaultRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);

  useEffect(() => { api.list(kind).then(setRows).catch((e) => setMsg(e.message)); }, [kind, refresh]);

  const decompress = async (name: string) => {
    setBusy(name);
    setMsg(null);
    try {
      const job = await api.decompress(name);
      const done = await follow(job.id, () => {});
      setMsg(done.state === "done" ? `restored ${(done.result as { restored: string[] }).restored.join(", ")}` : `error: ${done.error}`);
      onChange();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const remove = async (name: string) => {
    if (!window.confirm(`Delete ${name}?`)) return;
    setLeaving(name);  // slide the row out before the list refreshes
    await Promise.all([api.remove(kind, name).catch((e) => setMsg(e.message)), new Promise((r) => setTimeout(r, 200))]);
    setLeaving(null);
    onChange();
  };

  return (
    <Panel title={`${kind}/ [${rows.length}]`}>
      {msg && <p key={msg} className={`slide-in mb-3 font-mono text-xs ${msg.startsWith("error") ? "text-pop" : "text-cyan"}`}>» {msg}</p>}
      {rows.length === 0 ? <p className="font-mono text-sm text-dim">empty</p> : (
        <ul className="divide-y divide-line">
          {rows.map((r, k) => (
            <li key={r.name} style={{ animationDelay: `${Math.min(k, 10) * 40}ms` }}
              className={`rise flex flex-wrap items-center justify-between gap-3 py-3 ${leaving === r.name ? "row-out" : ""}`}>
              <div className="min-w-0">
                <div className="truncate font-mono text-sm">{r.name}</div>
                <div className="font-mono text-[11px] text-dim">
                  {fmtBytes(r.size)}
                  {r.original_size !== undefined && <> · from {fmtBytes(r.original_size)} · <span className="text-cyan">
                    -{(100 * (1 - r.size / Math.max(1, r.original_size))).toFixed(1)}%</span> · {r.mode}</>}
                  {r.error && <span className="text-pop"> · {r.error}</span>}
                  {" · "}{new Date(r.modified * 1000).toLocaleString()}
                </div>
              </div>
              <div className="flex gap-2">
                <a href={api.downloadUrl(kind, r.name)}><Btn kind="ghost">↓</Btn></a>
                {kind === "compressed" && (
                  <Btn kind="ghost" onClick={() => decompress(r.name)} disabled={busy !== null} busy={busy === r.name}>
                    {busy === r.name ? "…" : "decompress"}
                  </Btn>
                )}
                <Btn kind="danger" onClick={() => remove(r.name)}>del</Btn>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function Vault() {
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((t) => t + 1), []);
  return (
    <div className="grid gap-6 xl:grid-cols-2">
      <List kind="compressed" refresh={tick} onChange={bump} />
      <List kind="decompressed" refresh={tick} onChange={bump} />
    </div>
  );
}
