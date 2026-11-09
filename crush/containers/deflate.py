r"""Bit-exact deflate reconstruction ("re-deflate exactness").

Deflate data inside DOCX/PNG/PDF is already compressed, so modelling it directly
gains nothing.  To compress it well we must store the *inflated* data instead,
which is only lossless if we can regenerate the original compressed bits
exactly.  Two strategies, tried in order:

1. **zlib parameter search** (:func:`find_zlib_params`).  If some
   ``(level, memLevel, strategy)`` makes zlib produce the identical bytes, store
   just those parameters (3 bytes).  Works for files written by zlib-based tools
   (LibreOffice, python-docx, most PDF producers).

2. **Token reconstruction** (:func:`analyse` / :func:`rebuild`).  Encoders such
   as Microsoft Word use their own deflater, so (1) fails.  We then parse the
   original stream into its blocks and LZ77 tokens and store the *difference*
   between that parse and a deterministic prediction computed from the inflated
   data, which the decoder has anyway:

   * for each token position ``i`` the predictor lists every earlier position
     ``j`` (nearest first, within the 32 KiB window, up to ``MAX_CHAIN``) whose
     next 3 bytes equal ``data[i:i+3]``, with its match length ``len_j``;
     ``L* = max len_j``;
   * a literal is coded as symbol 0; a match ``(len, dist)`` with
     ``len = L*`` at the first candidate reaching ``L*`` as symbol 1; any other
     match as symbol 2 + ``L* - len`` + the rank of ``dist`` among candidates
     with ``len_j >= len``; a match outside the candidate list as symbol 3 +
     explicit length and distance; end-of-block as symbol 4.
   * block headers (type, final flag, the exact bits of a dynamic Huffman
     header, stored-block padding and lengths) are copied verbatim.

   Given the inflated data and this side stream, :func:`rebuild` re-runs the
   predictor, recovers every token, and re-emits the Huffman codes exactly.
   Typical side-stream size is a few percent of the inflated data, and it is
   itself CM-compressed.  Every reconstruction is verified before it is trusted.
"""

from __future__ import annotations

import zlib

import numpy as np
from numba import njit

MAX_CHAIN = 2048
WINDOW = 32768

LBASE = np.array([3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83,
                  99, 115, 131, 163, 195, 227, 258], dtype=np.int64)
LEXT = np.array([0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0],
                dtype=np.int64)
DBASE = np.array([1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025,
                  1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577], dtype=np.int64)
DEXT = np.array([0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12,
                 12, 13, 13], dtype=np.int64)
CL_ORDER = np.array([16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15], dtype=np.int64)


# --------------------------------------------------------------------------- zlib search

_STRATEGIES = (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED)


def _try(data: bytes, raw: bytes, level: int, mem: int, strategy: int, wbits: int) -> bool:
    """Compress incrementally and stop at the first byte that differs (cheap rejection)."""
    co = zlib.compressobj(level, zlib.DEFLATED, wbits, mem, strategy)
    pos = 0
    step = 1 << 15
    for k in range(0, len(data), step):
        chunk = co.compress(data[k:k + step])
        if chunk:
            if raw[pos:pos + len(chunk)] != chunk:
                return False
            pos += len(chunk)
    chunk = co.flush()
    return raw[pos:] == chunk and pos + len(chunk) == len(raw)


def find_zlib_params(data: bytes, raw: bytes, wbits: int = -15) -> tuple[int, int, int] | None:
    """Return ``(level, memLevel, strategy)`` such that zlib reproduces ``raw`` from ``data``."""
    for mem in (8, 9, 7, 6, 5, 4, 3, 2, 1):
        for level in (6, 9, 1, 2, 3, 4, 5, 7, 8):
            for strategy in _STRATEGIES:
                if _try(data, raw, level, mem, strategy, wbits):
                    return level, mem, strategy
        if mem == 8 and len(raw) > 1 << 20:
            break  # large members: non-default memLevels are rare, skip the long tail
    return None


def zlib_deflate(data: bytes, params: tuple[int, int, int], wbits: int = -15) -> bytes:
    level, mem, strategy = params
    co = zlib.compressobj(level, zlib.DEFLATED, wbits, mem, strategy)
    return co.compress(data) + co.flush()


# --------------------------------------------------------------------------- bit I/O + Huffman

@njit(cache=True)
def _bits(src, bitpos, n):
    """Read n bits LSB-first starting at absolute bit position ``bitpos`` (returns -1 past end)."""
    v = 0
    for k in range(n):
        byte = (bitpos + k) >> 3
        if byte >= src.shape[0]:
            return -1
        v |= ((np.int64(src[byte]) >> ((bitpos + k) & 7)) & 1) << k
    return v


@njit(cache=True)
def _build(lengths, nsym, count, symbol):
    """puff.c-style canonical Huffman decode tables.  Returns False if over-subscribed."""
    for L in range(16):
        count[L] = 0
    for s in range(nsym):
        count[lengths[s]] += 1
    left = 1
    for L in range(1, 16):
        left = (left << 1) - count[L]
        if left < 0:
            return False
    offs = np.zeros(16, dtype=np.int64)
    for L in range(1, 15):
        offs[L + 1] = offs[L] + count[L]
    for s in range(nsym):
        if lengths[s] != 0:
            symbol[offs[lengths[s]]] = s
            offs[lengths[s]] += 1
    return True


@njit(cache=True)
def _decode_sym(src, bp, count, symbol):
    """Decode one symbol; returns (symbol, new bitpos) or (-1, bp) on error."""
    code = 0
    first = 0
    index = 0
    for L in range(1, 16):
        byte = bp >> 3
        if byte >= src.shape[0]:
            return -1, bp
        code |= (np.int64(src[byte]) >> (bp & 7)) & 1
        bp += 1
        cnt = count[L]
        if code - cnt < first:
            return symbol[index + (code - first)], bp
        index += cnt
        first += cnt
        first <<= 1
        code <<= 1
    return -1, bp


@njit(cache=True)
def _read_dynamic_header(src, bp, litlen, dist):
    """Parse a dynamic block header at bit ``bp``; fill code lengths; return (hlit, hdist, new bp)."""
    hlit = _bits(src, bp, 5)
    hdist = _bits(src, bp + 5, 5)
    hclen = _bits(src, bp + 10, 4)
    if hlit < 0 or hdist < 0 or hclen < 0:
        return -1, -1, bp
    hlit += 257
    hdist += 1
    hclen += 4
    bp += 14
    cl = np.zeros(19, dtype=np.int64)
    for k in range(hclen):
        v = _bits(src, bp, 3)
        if v < 0:
            return -1, -1, bp
        cl[CL_ORDER[k]] = v
        bp += 3
    ccount = np.zeros(16, dtype=np.int64)
    csym = np.zeros(19, dtype=np.int64)
    if not _build(cl, 19, ccount, csym):
        return -1, -1, bp
    lens = np.zeros(320, dtype=np.int64)
    k = 0
    while k < hlit + hdist:
        s, bp = _decode_sym(src, bp, ccount, csym)
        if s < 0:
            return -1, -1, bp
        if s < 16:
            lens[k] = s
            k += 1
        else:
            rep = 0
            val = 0
            if s == 16:
                if k == 0:
                    return -1, -1, bp
                val = lens[k - 1]
                rep = 3 + _bits(src, bp, 2)
                bp += 2
            elif s == 17:
                rep = 3 + _bits(src, bp, 3)
                bp += 3
            else:
                rep = 11 + _bits(src, bp, 7)
                bp += 7
            if k + rep > hlit + hdist:
                return -1, -1, bp
            for _ in range(rep):
                lens[k] = val
                k += 1
    for s in range(320):
        litlen[s] = 0
        dist[s] = 0
    for s in range(hlit):
        litlen[s] = lens[s]
    for s in range(hdist):
        dist[s] = lens[hlit + s]
    return hlit, hdist, bp


@njit(cache=True)
def _fixed_lengths(litlen, dist):
    for s in range(320):
        litlen[s] = 0
        dist[s] = 0
    for s in range(144):
        litlen[s] = 8
    for s in range(144, 256):
        litlen[s] = 9
    for s in range(256, 280):
        litlen[s] = 7
    for s in range(280, 288):
        litlen[s] = 8
    for s in range(30):
        dist[s] = 5


# --------------------------------------------------------------------------- inflate with token log

@njit(cache=True)
def _inflate(src, out, tok_len, tok_dist, blk):
    """Inflate raw deflate ``src`` into ``out`` logging every token.

    tok_len[t] = 0 literal, >0 match length, -1 end of block.  ``blk`` rows:
    (bfinal, btype, header start bit, header end bit, stored pad value).
    Returns (out_len, n_tokens, n_blocks, end_bit) or negative error codes:
    -1 corrupt / unsupported, -2 output buffer too small.
    """
    bp = 0
    op = 0
    nt = 0
    nb = 0
    litlen = np.zeros(320, dtype=np.int64)
    dist = np.zeros(320, dtype=np.int64)
    lcount = np.zeros(16, dtype=np.int64)
    lsym = np.zeros(320, dtype=np.int64)
    dcount = np.zeros(16, dtype=np.int64)
    dsym = np.zeros(320, dtype=np.int64)
    cap = out.shape[0]
    while True:
        hdr = _bits(src, bp, 3)
        if hdr < 0 or nb >= blk.shape[0]:
            return -1, 0, 0, 0
        bfinal = hdr & 1
        btype = hdr >> 1
        blk[nb, 0] = bfinal
        blk[nb, 1] = btype
        hstart = bp + 3
        bp += 3
        if btype == 0:
            pad = (8 - (bp & 7)) & 7
            blk[nb, 4] = _bits(src, bp, pad) if pad else 0
            bp += pad
            byte = bp >> 3
            if byte + 4 > src.shape[0]:
                return -1, 0, 0, 0
            ln = np.int64(src[byte]) | (np.int64(src[byte + 1]) << 8)
            nln = np.int64(src[byte + 2]) | (np.int64(src[byte + 3]) << 8)
            if ln != (~nln & 0xFFFF) or byte + 4 + ln > src.shape[0]:
                return -1, 0, 0, 0
            if op + ln > cap:
                return -2, 0, 0, 0
            for k in range(ln):
                out[op] = src[byte + 4 + k]
                op += 1
            blk[nb, 2] = hstart
            blk[nb, 3] = ln
            bp = (byte + 4 + ln) * 8
            nb += 1
            if bfinal:
                break
            continue
        if btype == 1:
            _fixed_lengths(litlen, dist)
        elif btype == 2:
            hl, hd, bp = _read_dynamic_header(src, bp, litlen, dist)
            if hl < 0:
                return -1, 0, 0, 0
        else:
            return -1, 0, 0, 0
        blk[nb, 2] = hstart
        blk[nb, 3] = bp
        if not _build(litlen, 288, lcount, lsym):
            return -1, 0, 0, 0
        if not _build(dist, 30, dcount, dsym):
            return -1, 0, 0, 0
        while True:
            s, bp = _decode_sym(src, bp, lcount, lsym)
            if s < 0:
                return -1, 0, 0, 0
            if nt >= tok_len.shape[0]:
                return -2, 0, 0, 0
            if s < 256:
                if op >= cap:
                    return -2, 0, 0, 0
                out[op] = s
                op += 1
                tok_len[nt] = 0
                nt += 1
            elif s == 256:
                tok_len[nt] = -1
                nt += 1
                break
            else:
                s -= 257
                if s >= 29:
                    return -1, 0, 0, 0
                e = _bits(src, bp, LEXT[s])
                bp += LEXT[s]
                ln = LBASE[s] + e
                if s == 27 and e == 31:
                    return -1, 0, 0, 0  # length 258 coded non-canonically: not reproducible
                ds, bp = _decode_sym(src, bp, dcount, dsym)
                if ds < 0 or ds >= 30:
                    return -1, 0, 0, 0
                e = _bits(src, bp, DEXT[ds])
                bp += DEXT[ds]
                d = DBASE[ds] + e
                if d > op:
                    return -1, 0, 0, 0
                if op + ln > cap:
                    return -2, 0, 0, 0
                for _ in range(ln):
                    out[op] = out[op - d]
                    op += 1
                tok_len[nt] = ln
                tok_dist[nt] = d
                nt += 1
        nb += 1
        if bfinal:
            break
    return op, nt, nb, bp


# --------------------------------------------------------------------------- LZ77 predictor

@njit(cache=True)
def _chains(data, n):
    """Hash chains: prev[i] = previous position whose 3-byte prefix hashes like data[i:i+3].

    Chains may contain hash collisions; :func:`_candidates` verifies the bytes, and
    because encoder and decoder build identical chains the walk is deterministic."""
    prev = np.full(max(n, 1), -1, dtype=np.int64)
    head = np.full(1 << 20, -1, dtype=np.int64)
    for i in range(n - 2):
        key = (np.int64(data[i]) << 16) | (np.int64(data[i + 1]) << 8) | np.int64(data[i + 2])
        h = ((key * 0x9E3779B1) & 0xFFFFFFFF) >> 12
        prev[i] = head[h]
        head[h] = i
    return prev


@njit(cache=True)
def _candidates(data, n, prev, i, cd, cl):
    """Fill (distance, match length) of candidate matches at i, nearest first; returns (count, L*)."""
    nc = 0
    best = 0
    j = prev[i] if i < n - 2 else -1
    maxlen = min(258, n - i)
    steps = 0
    while j >= 0 and i - j <= WINDOW and steps < MAX_CHAIN:
        steps += 1
        if data[j] != data[i] or data[j + 1] != data[i + 1] or data[j + 2] != data[i + 2]:
            j = prev[j]
            continue
        L = 3
        while L < maxlen and data[j + L] == data[i + L]:
            L += 1
        cd[nc] = i - j
        cl[nc] = L
        nc += 1
        if L > best:
            best = L
        j = prev[j]
    return nc, best


@njit(cache=True)
def _put_varint(side, sp, v):
    while v >= 128:
        side[sp] = (v & 127) | 128
        sp += 1
        v >>= 7
    side[sp] = v
    return sp + 1


@njit(cache=True)
def _get_varint(side, sp):
    v = 0
    shift = 0
    while True:
        b = np.int64(side[sp])
        sp += 1
        v |= (b & 127) << shift
        shift += 7
        if b < 128:
            return v, sp


@njit(cache=True)
def _make_side(src, data, n, tok_len, tok_dist, nt, blk, nb, end_bit, side):
    """Encode the parse of ``src`` as a side stream relative to the predictor; returns length."""
    prev = _chains(data, n)
    cd = np.zeros(MAX_CHAIN, dtype=np.int64)
    cl = np.zeros(MAX_CHAIN, dtype=np.int64)
    sp = 0
    t = 0
    pos = 0
    for b in range(nb):
        bfinal = blk[b, 0]
        btype = blk[b, 1]
        side[sp] = bfinal | (btype << 1)
        sp += 1
        if btype == 0:
            side[sp] = blk[b, 4]
            sp += 1
            ln = blk[b, 3]
            sp = _put_varint(side, sp, ln)
            pos += ln
            continue
        if btype == 2:
            hs = blk[b, 2]
            he = blk[b, 3]
            nbits = he - hs
            sp = _put_varint(side, sp, nbits)
            for k in range(0, nbits, 8):
                m = min(8, nbits - k)
                side[sp] = _bits(src, hs + k, m)
                sp += 1
        while True:
            ln = tok_len[t]
            if ln < 0:
                side[sp] = 4
                sp += 1
                t += 1
                break
            if ln == 0:
                side[sp] = 0
                sp += 1
                pos += 1
                t += 1
                continue
            d = tok_dist[t]
            nc, best = _candidates(data, n, prev, pos, cd, cl)
            rank = -1
            r = 0
            for k in range(nc):
                if cl[k] >= ln:
                    if cd[k] == d:
                        rank = r
                        break
                    r += 1
            if rank < 0:
                side[sp] = 3
                side[sp + 1] = ln - 3
                side[sp + 2] = d & 255
                side[sp + 3] = d >> 8
                sp += 4
            elif ln == best and rank == 0:
                side[sp] = 1
                sp += 1
            else:
                side[sp] = 2
                side[sp + 1] = best - ln
                sp = _put_varint(side, sp + 2, rank)
            pos += ln
            t += 1
    # padding bits after the final block
    pad = (8 - (end_bit & 7)) & 7
    side[sp] = _bits(src, end_bit, pad) if pad else 0
    sp += 1
    return sp


# --------------------------------------------------------------------------- re-encoder

@njit(cache=True)
def _codes(lengths, nsym, codes):
    """Canonical Huffman codes, bit-reversed for LSB-first emission."""
    count = np.zeros(16, dtype=np.int64)
    for s in range(nsym):
        count[lengths[s]] += 1
    count[0] = 0
    nxt = np.zeros(16, dtype=np.int64)
    code = 0
    for L in range(1, 16):
        code = (code + count[L - 1]) << 1
        nxt[L] = code
    for s in range(nsym):
        L = lengths[s]
        if L:
            c = nxt[L]
            nxt[L] += 1
            r = 0
            for k in range(L):
                r = (r << 1) | ((c >> k) & 1)
            codes[s] = r


@njit(cache=True)
def _rebuild(data, n, side, nside, out):
    """Regenerate the original deflate bytes from inflated ``data`` and the side stream.

    Returns the number of bytes written, or -1 if the side stream is inconsistent."""
    prev = _chains(data, n)
    cd = np.zeros(MAX_CHAIN, dtype=np.int64)
    cl = np.zeros(MAX_CHAIN, dtype=np.int64)
    litlen = np.zeros(320, dtype=np.int64)
    dist = np.zeros(320, dtype=np.int64)
    lcode = np.zeros(320, dtype=np.int64)
    dcode = np.zeros(320, dtype=np.int64)
    hbuf = np.zeros(1024, dtype=np.uint8)
    acc = 0
    nacc = 0
    op = 0
    sp = 0
    pos = 0
    cap = out.shape[0]
    while True:
        if sp >= nside:
            return -1
        hdr = np.int64(side[sp])
        sp += 1
        bfinal = hdr & 1
        btype = hdr >> 1
        acc |= (bfinal | (btype << 1)) << nacc
        nacc += 3
        if btype == 0:
            padv = np.int64(side[sp])
            sp += 1
            pad = (8 - (nacc & 7)) & 7
            acc |= padv << nacc
            nacc += pad
            while nacc >= 8:
                if op >= cap:
                    return -1
                out[op] = acc & 255
                op += 1
                acc >>= 8
                nacc -= 8
            ln, sp = _get_varint(side, sp)
            if op + 4 + ln > cap or pos + ln > n:
                return -1
            out[op] = ln & 255
            out[op + 1] = ln >> 8
            out[op + 2] = (~ln) & 255
            out[op + 3] = ((~ln) >> 8) & 255
            op += 4
            for _ in range(ln):
                out[op] = data[pos]
                op += 1
                pos += 1
            if bfinal:
                break
            continue
        if btype == 1:
            _fixed_lengths(litlen, dist)
        else:
            nbits, sp = _get_varint(side, sp)
            nby = (nbits + 7) >> 3
            if nby > hbuf.shape[0]:
                return -1
            for k in range(nby):
                hbuf[k] = side[sp + k]
            for k in range(0, nbits, 8):
                m = min(8, nbits - k)
                acc |= (np.int64(side[sp]) & ((1 << m) - 1)) << nacc
                nacc += m
                sp += 1
                while nacc >= 8:
                    if op >= cap:
                        return -1
                    out[op] = acc & 255
                    op += 1
                    acc >>= 8
                    nacc -= 8
            hl, hd, _ = _read_dynamic_header(hbuf, 0, litlen, dist)
            if hl < 0:
                return -1
        _codes(litlen, 288, lcode)
        _codes(dist, 30, dcode)
        while True:
            sym = np.int64(side[sp])
            sp += 1
            if sym == 4:
                acc |= lcode[256] << nacc
                nacc += litlen[256]
            elif sym == 0:
                if pos >= n:
                    return -1
                c = np.int64(data[pos])
                acc |= lcode[c] << nacc
                nacc += litlen[c]
                pos += 1
            else:
                if sym == 3:
                    ln = np.int64(side[sp]) + 3
                    d = np.int64(side[sp + 1]) | (np.int64(side[sp + 2]) << 8)
                    sp += 3
                else:
                    nc, best = _candidates(data, n, prev, pos, cd, cl)
                    if sym == 1:
                        ln = best
                        rank = 0
                    else:
                        ln = best - np.int64(side[sp])
                        rank, sp = _get_varint(side, sp + 1)
                    d = -1
                    r = 0
                    for k in range(nc):
                        if cl[k] >= ln:
                            if r == rank:
                                d = cd[k]
                                break
                            r += 1
                    if d < 0:
                        return -1
                lc = 0
                while lc < 28 and LBASE[lc + 1] <= ln:
                    lc += 1
                sym_l = 257 + lc
                acc |= lcode[sym_l] << nacc
                nacc += litlen[sym_l]
                acc |= (ln - LBASE[lc]) << nacc
                nacc += LEXT[lc]
                dc = 0
                while dc < 29 and DBASE[dc + 1] <= d:
                    dc += 1
                acc |= dcode[dc] << nacc
                nacc += dist[dc]
                acc |= (d - DBASE[dc]) << nacc
                nacc += DEXT[dc]
                pos += ln
            while nacc >= 8:
                if op >= cap:
                    return -1
                out[op] = acc & 255
                op += 1
                acc >>= 8
                nacc -= 8
            if sym == 4:
                break
        if bfinal:
            break
    # final padding bits
    padv = np.int64(side[sp])
    pad = (8 - (nacc & 7)) & 7
    acc |= padv << nacc
    nacc += pad
    while nacc > 0:
        if op >= cap:
            return -1
        out[op] = acc & 255
        op += 1
        acc >>= 8
        nacc -= 8
    return op


# --------------------------------------------------------------------------- public API

def inflate_raw(raw: bytes) -> tuple[bytes, int] | None:
    """Inflate raw deflate with zlib; returns (data, bytes consumed) or None."""
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(raw)
    except zlib.error:
        return None
    if not d.eof:
        return None
    return out, len(raw) - len(d.unused_data)


def analyse(raw: bytes, data: bytes) -> bytes | None:
    """Return the side stream that lets :func:`rebuild` regenerate ``raw`` from ``data``.

    ``raw`` must be exactly one complete raw-deflate stream whose inflation is ``data``.
    Returns None when the stream cannot be reproduced (e.g. non-canonical codes).
    """
    src = np.frombuffer(raw, dtype=np.uint8)
    n = len(data)
    out = np.zeros(n + 16, dtype=np.uint8)
    tok_len = np.zeros(n + len(raw) + 16, dtype=np.int64)
    tok_dist = np.zeros_like(tok_len)
    blk = np.zeros((len(raw) // 2 + 16, 5), dtype=np.int64)
    olen, nt, nb, end_bit = _inflate(src, out, tok_len, tok_dist, blk)
    if olen != n or out[:n].tobytes() != data or (end_bit + 7) // 8 != len(raw):
        return None
    side = np.zeros(nt * 4 + nb * 1100 + 64, dtype=np.uint8)
    darr = np.frombuffer(data, dtype=np.uint8) if n else np.zeros(1, dtype=np.uint8)
    ns = _make_side(src, darr, n, tok_len, tok_dist, nt, blk, nb, end_bit, side)
    side_b = side[:ns].tobytes()
    if rebuild(data, side_b, len(raw)) != raw:
        return None
    return side_b


def rebuild(data: bytes, side: bytes, raw_len: int) -> bytes | None:
    """Inverse of :func:`analyse`."""
    n = len(data)
    darr = np.frombuffer(data, dtype=np.uint8) if n else np.zeros(1, dtype=np.uint8)
    sarr = np.frombuffer(side, dtype=np.uint8)
    out = np.zeros(raw_len + 16, dtype=np.uint8)
    k = _rebuild(darr, n, sarr, len(side), out)
    if k != raw_len:
        return None
    return out[:k].tobytes()
