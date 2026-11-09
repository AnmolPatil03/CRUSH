# CRUSH//

A context-mixing compressor for **.txt, .docx and .pdf**, written in Python 3.13 + Numba, with a
dark terminal-style web UI. Lossless by default: every restored file is checked byte-for-byte with
SHA-256.

**Results on the bundled corpus** (28 files, 6.1 MB, including real Microsoft Word documents):

| format | CRUSH | best of zlib / bz2 / lzma / zstd |
|---|---:|---:|
| txt (2.4 MB) | **77.1 % saved** (1.83 bits/char) | 72.4 % (bz2) |
| docx (0.5 MB) | **41.8 %** | 20.8 % (zstd-19) |
| pdf (3.3 MB) | **47.4 %** | 27.7 % (lzma-9e) |
| all | **58.4 %** | 43.3 % (lzma-9e) |

Full tables, per-file numbers, speed, memory, the ablation and the image-codec comparison are in
[docs/BENCHMARKS.md](docs/BENCHMARKS.md). The trade-off is speed: CRUSH codes at roughly
0.1–0.6 MB/s, where zstd runs at several MB/s.

## How it works (short)

1. **Detect** the container by magic bytes.
2. **Unpack** DOCX (ZIP) and PDF, working from the raw bytes, and **inflate** every deflate
   stream. Each one gets a recipe that rebuilds the original compressed bits exactly, using a zlib
   parameter search or, for Word's non-zlib deflater, *token reconstruction* (a stored diff against
   a predicted LZ77 parse).
3. **Route** content into typed streams: text, XML, PDF operators, PDF objects, fonts, images,
   container metadata.
4. **Model** each stream with a bitwise context-mixing coder: orders 0–6, word, column/record
   and match models; three logistic mixers and two APM/SSE stages; a 32-bit arithmetic coder.
   JPEGs are transcoded losslessly with JPEG XL. Pixels go to the smallest of JPEG XL, CM and an
   experimental residual pyramid.
5. **Verify**: the archive is decompressed again and SHA-256 compared before CRUSH reports success.

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the derivations and diagrams.
[docs/FORMAT.md](docs/FORMAT.md) specifies the `.crush` format.

## Running on Windows (step by step)

Tested on Windows 10/11 (64-bit) with PowerShell.

### 1. Install the prerequisites
- **Python 3.13 (64-bit)** from <https://www.python.org/downloads/windows/>. In the installer, tick
  **"Add python.exe to PATH"**. Check it in a new PowerShell window:
  ```powershell
  py -3.13 --version
  ```
- **Git** (only needed to clone): <https://git-scm.com/download/win>.
- **Node.js 20+** is *not* required; the web UI ships prebuilt in `ui/dist`.

### 2. Get the code
```powershell
git clone <repository-url> D:\DataCompression
cd D:\DataCompression
```

### 3. Create a virtual environment and install
```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```
If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled on this system"), allow
local scripts once and activate again:
```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\Activate.ps1
```
(`requirements.txt` alone is enough to run CRUSH; `requirements-dev.txt` adds the test tools.)

### 4. Install the JPEG XL tools (recommended)
1. Download `jxl-x64-windows-static.zip` from <https://github.com/libjxl/libjxl/releases> (v0.11.x).
2. Extract it to a permanent folder, e.g. `C:\tools\jxl`. The programs are in its `bin` subfolder.
3. Create the settings file and point CRUSH at the tools:
   ```powershell
   notepad .env
   ```
   Click **Yes** to create the file, add these two lines (adjust the folder if you extracted it
   elsewhere), save and close:
   ```
   CRUSH_CJXL=C:\tools\jxl\bin\cjxl.exe
   CRUSH_DJXL=C:\tools\jxl\bin\djxl.exe
   ```
4. Check the tool runs:
   ```powershell
   & "C:\tools\jxl\bin\cjxl.exe" --version
   ```

Without these tools CRUSH still works; JPEG images are simply stored without transcoding.
`.env.example` lists every other setting.

### 5. Verify the installation
```powershell
pytest -q
```
All tests should pass in about a minute. The first run is slower because Numba compiles the
engine once and caches it.

### 6. Start CRUSH
Every new PowerShell window:
```powershell
cd D:\DataCompression
.venv\Scripts\Activate.ps1
```
Then either open the web UI:
```powershell
crush serve
```
and browse to **http://127.0.0.1:8000** (stop the server with `Ctrl+C`), or use the command line:
```powershell
crush compress "C:\path\to\report.docx"
crush decompress storage\compressed\report.docx.crush
crush info storage\compressed\report.docx.crush
crush bench corpus
```
Archives go to `storage\compressed\`, restored files to `storage\decompressed\`.

### Troubleshooting
| Problem | Fix |
|---|---|
| `crush` is not recognized | Activate the virtual environment, or run `python -m crush ...` |
| `py -3.13` not found | Install Python 3.13 from python.org (64-bit) |
| First compression takes 10–20 s | One-time Numba compilation; later runs are fast |
| UI footer shows "jpeg xl tools: missing" | Check the paths in `.env`, then restart `crush serve` |
| Port 8000 already in use | `crush serve --port 8080` |
| Page looks outdated after an update | Hard-refresh the browser with `Ctrl+F5` |

## Setup on Linux / macOS

Requires **Python 3.13** (the standard GIL build). All dependencies are pinned and ship Python
3.13 wheels for Windows, Linux (x86-64/ARM64) and macOS (Intel 13+, Apple Silicon 14+).

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt && pip install -e .
```

JPEG XL tools (optional, recommended):

| OS | install |
|---|---|
| macOS | `brew install jpeg-xl` |
| Debian/Ubuntu 24.04+ | `sudo apt install libjxl-tools` |
| Fedora | `sudo dnf install libjxl-utils` |
| any Linux | the static tarball `jxl-linux-x86_64-static-v0.11.1.tar.gz` from <https://github.com/libjxl/libjxl/releases> |

`cjxl`/`djxl` give lossless JPEG transcoding (about 20 % smaller JPEGs, bit-exact restore). An
archive that contains JPEG XL streams needs the tools to decompress. If they are not on `PATH`,
set `CRUSH_CJXL` / `CRUSH_DJXL` in `.env`.

### Configuration
Copy `.env.example` to `.env` to change the storage root (`uploads/`, `compressed/`,
`decompressed/`), tool paths, worker count, upload limit, host and port.

## Use

```bash
crush serve                                  # UI + API at http://127.0.0.1:8000
crush compress report.docx notes.txt         # one archive per file -> storage/compressed/
crush compress *.pdf --solid papers          # one solid archive (shared models, dedup)
crush compress scan.pdf --mode optimised     # lossy images, clearly labelled
crush decompress storage/compressed/report.docx.crush   # -> storage/decompressed/report.docx
crush info storage/compressed/report.docx.crush
crush bench corpus --md docs/BENCHMARKS.md --out docs/bench_results.json
```

(`python -m crush ...` works without installing the command.) Existing files are never
overwritten; a ` (1)`, ` (2)`, … suffix is added instead.

**Web UI screens:**
- **Compress**: drag and drop multiple files, choose Lossless or Optimised mode (Optimised shows a
  warning), and watch live progress for each stream.
- **Result**: sizes, % saved, ratio, bits/char, a per-stream breakdown, the SHA-256 badge and a
  download button.
- **Vault**: the `compressed/` and `decompressed/` panels, where you can download, decompress or
  delete files.
- **Benchmarks**: CRUSH against zlib, bz2, lzma and zstd on any folder.

The UI is prebuilt in `ui/dist`. To change it, install Node 20+, then run `cd ui && npm install`,
`npm run dev` (it proxies to `crush serve`) and `npm run build`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q          # 106 tests, about 1 minute
```

The suite covers:
- byte-exact round trips for every codec and container: empty, tiny, random and Unicode text;
  DOCX with tables and images; PDFs with fonts and images; real Word and PDF files from the corpus;
  odd and truncated ZIPs and PDFs;
- `hypothesis` property tests for the arithmetic coder, the CM engine and deflate reconstruction
  (all zlib levels and strategies, including stored and fixed-Huffman blocks);
- dedup, and parallel output matching serial output;
- errors for corrupt archives (bad magic, CRC, unknown codec, SHA mismatch);
- storage collision rules, the CLI, the HTTP API with SSE, and optimised mode.

## Limitations

- **Speed.** Pure Python + Numba context mixing is slow compared with C compressors (see
  benchmarks). Compression also runs a full verification pass.
- **Memory.** Expect about 400 MiB peak per worker process.
- **PDF stream filters.** Only `/FlateDecode` and `/DCTDecode` are unpacked. LZW, ASCII85,
  RunLength, CCITT and JBIG2 streams are coded as opaque bytes, which is why lzma still wins on
  two tiny ImageMagick and ReportLab samples.
- **Encryption.** Encrypted PDFs and DOCX files keep their stream bodies verbatim (still lossless,
  little gain).
- **Residual pyramid.** The experimental pyramid codec loses to JPEG XL on every image tested. It
  stays in for the benchmark.
