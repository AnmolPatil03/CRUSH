r"""Context-mixing (CM) compressor, compiled with Numba.

Every byte is coded as 8 binary decisions, MSB first.  For each decision a set
of models each output a probability ``p_i`` that the next bit is 1; a gated
logistic mixer combines them, two APM/SSE stages refine the result, and the
binary arithmetic coder of :mod:`crush.cm.reference` codes the bit.  The
decoder runs the *same* function with the same model updates, so it sees the
same probabilities and recovers the bits exactly.

Models (inputs to the mixer)
----------------------------
==  =========================================================================
0   order-0: context = partial byte ``c0`` only
1-6 order-k, k = 1..6: hash of the previous k bytes and ``c0``
7   word: hash of the current word (letters, case-folded) and ``c0``
8   word bigram: hash of current word, previous word and ``c0``
9   column: (column since last newline, byte directly above in the previous line)
10  column: (byte above, previous byte)
11  record: (byte one record back, previous byte); record length is either
    given (image row stride) or auto-detected from repeating byte distances
12  match: if the last 6 bytes occurred before, predict the bit that followed
    that occurrence; confidence is learned per match length
==  =========================================================================

Each context slot stores a 16-bit probability and a hit count ``n``; update

    p <- p + (y * 2^16 - p) * DT[n] / 2^16,   DT[n] = 2^16 / (n + 1.5)

Mixer (logistic mixing in the stretched domain)
-----------------------------------------------
With ``s_i = stretch(p_i)`` and weight vector ``w`` (selected by a small
context) the mixed prediction and its online gradient step are

    p = squash( sum_i w_i * s_i )
    w_i <- w_i + eta * (y - p) * s_i

which is exactly gradient descent on coding cost ``-log2 P(y)`` (the derivative
of the log-loss of a logistic unit w.r.t. ``w_i`` is ``(p - y) * s_i``).  Three
weight sets are used, selected by (match state, c0), by the previous byte and
by (number of orders 1-6 whose context has been seen before, c0); their
outputs are averaged in the stretched domain.

APM / SSE
---------
An adaptive probability map refines ``p`` given a context ``c``: the stretched
``p`` is quantised into 33 buckets, the output is the linear interpolation of
the two nearest bucket values for ``c``, and the nearer bucket is pulled
towards the coded bit.  Two APMs (order-1 and hashed order-2 contexts) are
averaged with the mixer output.

All arithmetic is integer, so output is identical on every platform.
"""

from __future__ import annotations

import numpy as np
from numba import njit

from .tables import SQUASH, STRETCH

F_MIXER = 1 << 1    # learn mixer weights (off: fixed equal weights)
F_SSE = 1 << 2      # APM/SSE refinement
F_WORD = 1 << 3     # word models
F_RECORD = 1 << 4   # column + record models
F_MATCH = 1 << 5    # match model
FULL = F_MIXER | F_SSE | F_WORD | F_RECORD | F_MATCH

N_INPUTS = 13
_LIMIT = 255
DT = np.array([65536 * 2 // (2 * n + 3) for n in range(_LIMIT + 1)], dtype=np.int64)
M32 = 0xFFFFFFFF


def table_bits(n: int) -> int:
    """Default hash-table size (log2 slots per model) for an input of ``n`` bytes.

    11 tables x 2^22 slots x 4 bytes = 176 MiB at the cap; small inputs use far less."""
    b = max(n, 1).bit_length() + 4
    return min(22, max(16, b))


@njit(cache=True, inline="always")
def _h(a: np.int64, b: np.int64) -> np.int64:
    return ((a * 0x2F0B4C27 + b + 0x6F4F2A1) * 0x9E3779B1) & 0xFFFFFFFF


@njit(cache=True, inline="always")
def _sq(x: np.int64, SQ: np.ndarray) -> np.int64:
    if x > 2047:
        x = 2047
    if x < -2047:
        x = -2047
    return SQ[x + 2047]


@njit(cache=True)
def _run(buf, n, comp, decoding, flags, rlen_fixed, tb, lr, SQ, ST, DTab):
    """Encode ``buf[:n]`` into ``comp`` (returns bytes written, -1 if it would not shrink),
    or decode ``comp`` into ``buf[:n]`` (returns 0)."""
    # Hashed slots are grouped in 16-slot buckets, one bucket per (context, nibble):
    # the 4 bits of a nibble then hit one cache line instead of four random ones.
    # Each slot packs probability (high 16 bits) and hit count (low 8 bits).
    tsize = 1 << tb
    bshift = 32 - (tb - 4)
    T = np.full((12, tsize), 32768 << 8, dtype=np.uint32)
    T0 = np.full(256, 32768 << 8, dtype=np.int64)
    base = np.zeros(N_INPUTS, dtype=np.int64)
    idx = np.zeros(N_INPUTS, dtype=np.int64)
    st = np.zeros(N_INPUTS, dtype=np.int64)
    active = np.zeros(N_INPUTS, dtype=np.bool_)
    for k in range(7):
        active[k] = True
    if flags & F_WORD:
        active[7] = True
        active[8] = True
    if flags & F_RECORD:
        active[9] = True
        active[10] = True
        active[11] = True
    use_match = (flags & F_MATCH) != 0
    learn = (flags & F_MIXER) != 0
    use_sse = (flags & F_SSE) != 0

    # mixer weights: set 1 = (match bucket 0..3) * 256 + c0, set 2 = previous byte
    W1 = np.full((1024, N_INPUTS), 22000, dtype=np.int64)
    W2 = np.full((256, N_INPUTS), 22000, dtype=np.int64)
    W3 = np.full((7 * 256, N_INPUTS), 22000, dtype=np.int64)
    # APMs: 33 interpolation knots per context, initialised to the identity map
    A1 = np.zeros(65536 * 33, dtype=np.int32)
    A2 = np.zeros(65536 * 33, dtype=np.int32)
    for j in range(33):
        v = SQ[min(4094, max(0, (j - 16) * 128 + 2047))]
        for c in range(65536):
            A1[c * 33 + j] = v
            A2[c * 33 + j] = v
    # match model
    mbits = min(22, tb)
    MT = np.zeros(1 << mbits, dtype=np.int64)
    MP = np.full(64, 32768, dtype=np.int64)
    MC = np.zeros(64, dtype=np.int64)
    mptr = 0
    mlen = 0
    # record detection
    lastpos = np.zeros(256, dtype=np.int64)
    dist1 = np.zeros(256, dtype=np.int64)
    rcand = 0
    rcount = 0
    rlen = rlen_fixed
    # word / column state
    wh = 0
    pw = 0
    line_start = 0
    prev_line = 0
    hb = np.zeros(N_INPUTS, dtype=np.int64)

    x1 = 0
    x2 = 0xFFFFFFFF
    x = 0
    op = 0
    cap = comp.shape[0]
    if decoding:
        for _ in range(4):
            x = (x << 8) | (np.int64(comp[op]) if op < cap else 0)
            op += 1

    for i in range(n):
        # ---- byte-boundary context hashes -------------------------------
        c1 = np.int64(buf[i - 1]) if i >= 1 else 0
        acc = 0
        for k in range(1, 7):
            b = np.int64(buf[i - k]) + 1 if i >= k else 0
            acc = _h(acc, b + (k << 9))
            hb[k] = acc
        hb[7] = _h(wh + (c1 << 32 if wh == 0 else 0), 7)
        hb[8] = _h(_h(wh, pw), 8)
        col = i - line_start
        above = 0
        if prev_line + col < line_start:
            above = np.int64(buf[prev_line + col])
        hb[9] = _h((min(col, 255) << 8) | above, 9)
        hb[10] = _h((above << 8) | c1, 10)
        rab = np.int64(buf[i - rlen]) if (rlen > 0 and i >= rlen) else 0
        hb[11] = _h(((rab << 8) | c1) + (rlen << 16), 11)
        mb = 0
        if mlen > 0:
            mb = 1 if mlen < 8 else (2 if mlen < 16 else 3)
        mexp = np.int64(buf[mptr]) if mlen > 0 else 0
        mvalid = mlen > 0

        c0 = 1
        nib = 1
        c = np.int64(buf[i]) if not decoding else 0
        for bitpos in range(8):
            # ---- predictions ---------------------------------------------
            if bitpos == 0 or bitpos == 4:
                hn = (c0 * 0x9E3779B1) & 0xFFFFFFFF
                for k in range(1, 12):
                    base[k] = ((((hb[k] ^ hn) * 0x85EBCA6B) & 0xFFFFFFFF) >> bshift) << 4
                nib = 1
            hc = (c0 * 0x9E3779B1) & 0xFFFFFFFF
            st[0] = ST[T0[c0] >> 12]
            seen = 0
            for k in range(1, 12):
                if active[k]:
                    j = base[k] + nib
                    idx[k] = j
                    v = np.int64(T[k, j])
                    st[k] = ST[v >> 12]
                    if k <= 6 and (v & 255) != 0:
                        seen += 1
                else:
                    st[k] = 0
            ebit = 0
            mi = -1
            if use_match and mvalid:
                ebit = (mexp >> (7 - bitpos)) & 1
                mi = min(mlen, 31) * 2 + ebit
                st[12] = ST[MP[mi] >> 4]
            else:
                st[12] = 0
            s1 = mb * 256 + c0
            s3 = seen * 256 + c0
            dot1 = 0
            dot2 = 0
            dot3 = 0
            for k in range(N_INPUTS):
                dot1 += st[k] * W1[s1, k]
                dot2 += st[k] * W2[c1, k]
                dot3 += st[k] * W3[s3, k]
            d1 = dot1 >> 16
            d2 = dot2 >> 16
            d3 = dot3 >> 16
            pm1 = _sq(d1, SQ)
            pm2 = _sq(d2, SQ)
            pm3 = _sq(d3, SQ)
            pr = _sq((d1 + d2 + d3) // 3, SQ)
            if use_sse:
                s = ST[pr >> 4] + 2048
                if s > 4095:
                    s = 4095
                lo = s >> 7
                wgt = s & 127
                a1i = (c0 | (c1 << 8)) * 33 + lo
                a2c = ((hb[2] ^ hc) * 0x85EBCA6B & 0xFFFFFFFF) >> 16
                a2i = a2c * 33 + lo
                pa1 = (np.int64(A1[a1i]) * (128 - wgt) + np.int64(A1[a1i + 1]) * wgt) >> 7
                pa2 = (np.int64(A2[a2i]) * (128 - wgt) + np.int64(A2[a2i + 1]) * wgt) >> 7
                pf = (pr + pa1 + 2 * pa2) >> 2
            else:
                pf = pr
            if pf < 32:
                pf = 32
            if pf > 65504:
                pf = 65504

            # ---- code the bit ----------------------------------------------
            r = x2 - x1
            xmid = x1 + (r >> 16) * pf + (((r & 0xFFFF) * pf) >> 16)
            if decoding:
                y = 1 if x <= xmid else 0
            else:
                y = (c >> (7 - bitpos)) & 1
            if y:
                x2 = xmid
            else:
                x1 = xmid + 1
            while ((x1 ^ x2) & 0xFF000000) == 0:
                if decoding:
                    x = ((x << 8) & 0xFFFFFFFF) | (np.int64(comp[op]) if op < cap else 0)
                else:
                    if op >= cap:
                        return -1
                    comp[op] = x2 >> 24
                op += 1
                x1 = (x1 << 8) & 0xFFFFFFFF
                x2 = ((x2 << 8) & 0xFFFFFFFF) | 0xFF

            # ---- update -------------------------------------------------
            target = y << 16
            v = T0[c0]
            cnt = v & 255
            pv = v >> 8
            pv += ((target - pv) * DTab[cnt]) >> 16
            T0[c0] = (pv << 8) | (cnt + 1 if cnt < 255 else cnt)
            for k in range(1, 12):
                if active[k]:
                    j = idx[k]
                    v = np.int64(T[k, j])
                    cnt = v & 255
                    pv = v >> 8
                    pv += ((target - pv) * DTab[cnt]) >> 16
                    if cnt < 127:
                        cnt += 1
                    T[k, j] = (pv << 8) | cnt
            if mi >= 0:
                cnt = MC[mi]
                MP[mi] += ((target - MP[mi]) * DTab[cnt]) >> 16
                if cnt < 255:
                    MC[mi] = cnt + 1
                if ebit != y:
                    mvalid = False
            if learn:
                e1 = ((target - pm1) >> 4) * lr
                e2 = ((target - pm2) >> 4) * lr
                e3 = ((target - pm3) >> 4) * lr
                for k in range(N_INPUTS):
                    W1[s1, k] += (st[k] * e1) >> 14
                    W2[c1, k] += (st[k] * e2) >> 14
                    W3[s3, k] += (st[k] * e3) >> 14
            if use_sse:
                g = target + (y << 7) - y - y
                ia = a1i + (wgt >> 6)
                A1[ia] += (g - np.int64(A1[ia])) >> 6
                ib = a2i + (wgt >> 6)
                A2[ib] += (g - np.int64(A2[ib])) >> 6
            c0 = (c0 << 1) | y
            nib = (nib << 1) | y

        # ---- byte done --------------------------------------------------
        c = c0 & 0xFF
        if decoding:
            buf[i] = c
        # match model: extend or look up
        if mlen > 0 and np.int64(buf[mptr]) == c:
            mlen += 1
            mptr += 1
            if mlen > 65535:
                mlen = 65535
        else:
            mlen = 0
        if i >= 5:
            mh = (_h(hb[6], c + 1) >> (32 - mbits))
            if mlen == 0:
                cand = MT[mh]
                if cand > 0:
                    L = 0
                    while L < 32 and cand - 1 - L >= 0 and buf[cand - 1 - L] == buf[i - L]:
                        L += 1
                    if L >= 6:
                        mlen = L
                        mptr = cand
            MT[mh] = i + 1
        # word model
        lc = c + 32 if 65 <= c <= 90 else c
        if (97 <= lc <= 122) or c >= 128:
            wh = _h(wh, lc)
        elif wh != 0:
            pw = wh
            wh = 0
        # columns
        if c == 10:
            prev_line = line_start
            line_start = i + 1
        # record length detection (three equal spacings of the same byte value)
        if rlen_fixed == 0:
            d = i - lastpos[c]
            if d > 2 and d == dist1[c]:
                if d == rcand:
                    rcount += 1
                    if rcount >= 4 and rlen != d:
                        rlen = d
                else:
                    rcand = d
                    rcount = 1
            dist1[c] = d
            lastpos[c] = i

    if not decoding:
        for _ in range(4):
            if op >= cap:
                return -1
            comp[op] = (x1 >> 24) & 0xFF
            x1 = (x1 << 8) & 0xFFFFFFFF
            op += 1
        return op
    return 0


LEARNING_RATE = 12


def compress(data: bytes, flags: int = FULL, record_len: int = 0, tb: int | None = None) -> bytes | None:
    """CM-code ``data``.  Returns ``None`` when the result would not be smaller (store it).

    ``tb`` (log2 hash-table slots per model) defaults to :func:`table_bits`; the
    caller must record it, together with ``flags`` and ``record_len``, because the
    decoder needs the identical model configuration.
    """
    n = len(data)
    if n == 0:
        return b""
    buf = np.frombuffer(data, dtype=np.uint8).copy()
    comp = np.zeros(n, dtype=np.uint8)
    used = _run(buf, n, comp, False, flags, record_len, tb or table_bits(n), LEARNING_RATE,
                SQUASH, STRETCH, DT)
    if used < 0 or used >= n:
        return None
    return comp[:used].tobytes()


def decompress(payload: bytes, n: int, flags: int = FULL, record_len: int = 0, tb: int | None = None) -> bytes:
    """Inverse of :func:`compress` (``n`` = original length)."""
    if n == 0:
        return b""
    buf = np.zeros(n, dtype=np.uint8)
    comp = np.frombuffer(payload, dtype=np.uint8).copy()
    _run(buf, n, comp, True, flags, record_len, tb or table_bits(n), LEARNING_RATE,
         SQUASH, STRETCH, DT)
    return buf.tobytes()
