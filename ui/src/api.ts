// Typed client for the CRUSH FastAPI backend.

export type StreamRow = { type: string; codec: string; raw: number; packed: number; note: string };
export type FileRow = { name: string; container: string; original: number; sha256: string; restored_ok: boolean };
export type LossyImage = {
  image: string; from: [number, number]; to: [number, number]; dpi_before: number; dpi_after: number;
  bytes_before: number; bytes_after: number; format: string; kind: string; psnr_db: number;
};
export type Report = {
  archive: string; mode: "lossless" | "optimised"; files: FileRow[]; archive_size: number; original_size: number;
  streams: StreamRow[]; stats: Record<string, number>; dedup_bytes: number; seconds: number; verified: boolean;
  notes: string[]; saved_pct: number; ratio: number; bits_per_char?: number;
  optimised?: { file: string; changed: boolean; images: LossyImage[] }[];
};
export type Restored = { archive: string; archive_size: number; restored: string[];
  files: { name: string; size: number }[] };
export type Job = { id: string; kind: string; label: string; state: string; result: unknown; error: string | null };
export type JobEvent = { stage: string; done?: number; total?: number; stream?: string; raw?: number;
  packed?: number; codec?: string; file?: string; message?: string; state?: string; t: number };
export type VaultRow = { name: string; size: number; modified: number; original_size?: number; mode?: string;
  files?: { name: string; size: number }[]; error?: string };

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not JSON */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then(j<{ ok: boolean; jxl: boolean; storage: string }>),
  compress: (files: File[], mode: string) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("mode", mode);
    return fetch("/api/compress", { method: "POST", body: fd }).then(j<{ jobs: Job[] }>);
  },
  decompressUpload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    return fetch("/api/decompress-upload", { method: "POST", body: fd }).then(j<{ jobs: Job[] }>);
  },
  decompress: (name: string) => fetch(`/api/decompress/${encodeURIComponent(name)}`, { method: "POST" }).then(j<Job>),
  bench: (folder: string, ablation: boolean) =>
    fetch("/api/bench", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder, ablation }) }).then(j<Job>),
  list: (kind: "compressed" | "decompressed") => fetch(`/api/files/${kind}`).then(j<VaultRow[]>),
  remove: (kind: string, name: string) =>
    fetch(`/api/files/${kind}/${encodeURIComponent(name)}`, { method: "DELETE" }).then(j<unknown>),
  downloadUrl: (kind: string, name: string) => `/api/files/${kind}/${encodeURIComponent(name)}`,
};

/** Follow a job's Server-Sent Events until it finishes. */
export function follow(id: string, onEvent: (e: JobEvent) => void): Promise<Job> {
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/jobs/${id}/events`);
    es.onmessage = (m) => onEvent(JSON.parse(m.data));
    es.addEventListener("final", (m) => { es.close(); resolve(JSON.parse((m as MessageEvent).data)); });
    es.onerror = () => { es.close(); reject(new Error("lost connection to the server")); };
  });
}

export const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB"];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v < 10 ? 2 : 1)} ${u[i]}`;
};
