# The `.crush` archive format (version 1)

All integers are little-endian. `varint` is unsigned LEB128 (7 bits per byte, high bit = "more").

## 1. Layout

```
offset  field              type
------  -----------------  ------------------------------------------------
0       magic              6 bytes  "CRUSH" 0x1A
6       version            u8       = 1
7       mode               u8       0 = lossless, 1 = optimised (lossy images)
8       flags              u8       reserved, 0
9       n_files            varint
        file[n_files]:
          name_len         varint
          name             UTF-8 bytes (original file name, no directory)
          original_size    varint
          sha256           32 bytes  SHA-256 of the restored file
          container        u8       0 = text/bytes, 1 = ZIP/DOCX, 2 = PDF
        manifest           stream record (codec, params, raw_len, payload)
        n_streams          varint
        stream[n_streams]:
          type             u8       see table 3
          record           stream record
          crc32            u32      CRC-32 of the decoded (raw) stream
        trailer            u32      CRC-32 of every preceding byte
```

**Stream record**: `codec u8 · params_len varint · params · raw_len varint · payload_len varint · payload`.

The file table sits before any compressed data, so listing an archive (`crush info`, the Vault screen)
never decodes anything.

## 2. Codecs

| id | name | params | payload |
|---:|---|---|---|
| 0 | STORE | — | raw bytes (*stored mode*: codec byte = the 1-byte flag) |
| 1 | CM | `flags u8, table_bits u8, record_len varint` | arithmetic-coded bit stream (§5) |
| 2 | JXL_JPEG | — | JPEG XL file; `djxl` reconstructs the original JPEG bit-exactly |
| 3 | JXL_PIXELS | `w, h, c` varints | lossless JPEG XL; decodes to `h·w·c` bytes (row-major, interleaved) |
| 4 | PYRAMID | `w, h, c` varints | CM payload of the residual pyramid (docs/ARCHITECTURE.md §6) |

An unknown codec id is rejected with `UnknownCodec` ("upgrade CRUSH"); new codecs get new ids,
so old archives stay readable.

CM `flags` bits: `0x02` learned mixer, `0x04` APM/SSE, `0x08` word models, `0x10` column/record
models, `0x20` match model (`0x3E` = everything, the default). Order 0–6 models are always on.

## 3. Stream types

| id | name | contents |
|---:|---|---|
| 0 | TEXT | whole plain-text files |
| 1 | XML | inflated DOCX parts (`*.xml`, `*.rels`, SVG …) |
| 2 | PDF_OPS | inflated PDF content streams (drawing operators) |
| 3 | PDF_OBJ | PDF bytes outside streams (objects, dictionaries, xref, trailer), xref/object streams, zlib headers/Adler-32 |
| 4 | FONT | embedded fonts (PDF FontFile*, DOCX .odttf) |
| 5 | BINARY | everything else, including deflate data that cannot be rebuilt |
| 6 | IMAGE | **one image per stream** (JPEG or raw pixels) |
| 7 | META | ZIP local headers / central directory, PNG non-IDAT chunks, PNG filter bytes |
| 8 | DEFLATE_SIDE | deflate token-reconstruction side information |

Types 0–5, 7, 8 are **solid**: within one archive, data of the same type from every file is appended
into a single stream before modelling. Byte strings ≥ 64 bytes that occur twice are stored once
(SHA-256 deduplication); the second occurrence is just another reference.

## 4. Manifest (rebuild recipe)

The manifest is UTF-8 JSON, normally CM-coded: `{"v": 1, "files": [recipe, ...]}`, one recipe per
file-table entry. A recipe is an expression that evaluates to bytes:

| form | meaning |
|---|---|
| `["r", sid, off, n]` | `n` bytes of decoded stream `sid` starting at `off` |
| `["c", [r1, r2, …]]` | concatenation |
| `["z", src, level, memLevel, strategy]` | `zlib` raw deflate (`wbits = -15`) of `src` with these parameters |
| `["k", src, side, raw_len]` | deflate **token reconstruction** of `src` using side info `side`; output must be `raw_len` bytes |
| `["p", pre, post, [s1, s2, …], z]` | PNG: `pre` + IDAT chunks of sizes `s_i` cut from `z` (CRCs recomputed) + `post` |
| `["f", px, ftypes, w, h, c]` | PNG row filtering: apply filter type `ftypes[y]` to row `y` of pixels `px` |

Examples of what the recipes encode:

* **DOCX** – `["c", [meta, member₁, meta, member₂, …, central-directory]]` where each deflated member is
  `["z", xml-ref, 6, 8, 0]` (zlib reproduced it) or `["k", xml-ref, side-ref, 1234]` (token rebuild) or a plain
  reference to the original compressed bytes (neither worked). ZIP member order, local headers, extra
  fields and the central directory are reproduced verbatim from META.
* **PDF** – `["c", [obj-bytes, ["c", [zlib-hdr, ["z"|"k", ops-ref …], adler]], obj-bytes, image-ref, …]]`. Every
  byte outside a stream body is verbatim PDF_OBJ data, so offsets/xref never change.
* **PNG inside DOCX** – `["p", chunks-before, chunks-after, [idat sizes], ["c", [zlib-hdr, ["k", ["f", pixels-ref, filters-ref, w, h, c], side, n], adler]]]`.

## 5. CM payload

The CM coder (docs/ARCHITECTURE.md §3–5) codes `raw_len` bytes MSB-first. The arithmetic coder is a
32-bit carry-less binary coder with 16-bit probabilities; the payload ends with the 4 bytes of the
final low bound `x1`. The decoder needs `raw_len`, `flags`, `table_bits` and `record_len` (all stored),
and runs the identical model. All model arithmetic is integer, so archives are identical across
platforms and Numba versions (this was checked with numba 0.62.1 vs 0.68.1).

## 6. Integrity and errors

| check | error raised |
|---|---|
| first 6 bytes ≠ magic | `BadMagic` — "not a .crush archive" |
| version > 1 | `UnsupportedVersion` |
| trailer CRC-32 mismatch / truncated data | `CorruptArchive` |
| decoded stream length or CRC-32 mismatch | `CorruptArchive` ("stream k (TYPE) failed its CRC-32 check") |
| unknown codec id | `UnknownCodec` |
| JPEG XL stream but cjxl/djxl not installed | `ToolMissing` |
| SHA-256 of a restored file ≠ header | `ChecksumMismatch` |

## 7. Overhead bound ("never meaningfully larger")

A block whose CM output is not smaller than its input is stored (codec 0), and a block with order-0
entropy above 7.97 bits/byte skips CM entirely. The fixed cost of an archive is
`6 + 3 + 1` header bytes, per file `≈ 35 + len(name)` (SHA-256 + size + container), the manifest
(≈ 30–60 bytes for a single text file), and 6 + per-stream record bytes + 4 trailer bytes.
In practice: **an incompressible or empty file grows by at most ~140 bytes**, enforced by
`tests/test_pipeline.py::test_text_roundtrip` and `test_random_data_uses_stored_mode`.
