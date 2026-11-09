r"""Pure-Python reference implementation: binary arithmetic coder + order-2 model.

This is the readable, slow version of the coder used by the Numba engine
(:mod:`crush.cm.engine` uses exactly the same interval split, renormalisation
and flush, with a much richer model).  It documents the maths and serves as a
test oracle for the coder itself.

Arithmetic coding (carry-less, 32-bit)
--------------------------------------
The coder keeps an interval ``[x1, x2]`` of 32-bit integers.  To code a bit with
predicted probability ``p = P(bit = 1)`` (16-bit fixed point, ``0 < p < 65536``)
the interval is split at

    xmid = x1 + floor((x2 - x1) * p / 65536)

and the coder keeps ``[x1, xmid]`` for a 1 and ``[xmid + 1, x2]`` for a 0.  The
width of the kept part is proportional to the probability of the bit actually
coded, so after n bits the interval width is ~2^32 * prod(P(bit_i)), i.e. the
code length is ``-sum(log2 P(bit_i))`` bits, the Shannon information content.
Whenever the leading bytes of x1 and x2 agree they can never change again, so
they are shifted out ("renormalisation") and the interval re-expanded.

Order-2 model
-------------
Each byte is coded MSB first as 8 binary decisions.  The context of a decision is
the previous two bytes plus the bits of the current byte seen so far
(``c0``, a 1-prefixed partial byte in 1..255).  Each context owns a 16-bit
probability updated after every bit with an adaptive rate:

    p <- p + (y*65536 - p) / (n + 1.5),   n <- min(n + 1, LIMIT)

which is a running average for the first LIMIT observations and an exponential
moving average afterwards.
"""

from __future__ import annotations

from array import array

PROB_BITS = 16
PROB_ONE = 1 << PROB_BITS
LIMIT = 127
# Reciprocal table: DT[n] ~ 65536 / (n + 1.5); integer, so results are exact.
DT = [int(65536 // (n + 1.5)) for n in range(LIMIT + 1)]


def split(x1: int, x2: int, p: int) -> int:
    """Interval split point for a 16-bit probability ``p`` of a 1 bit (exact integer maths)."""
    r = x2 - x1
    return x1 + (r >> 16) * p + (((r & 0xFFFF) * p) >> 16)


class Encoder:
    def __init__(self) -> None:
        self.x1, self.x2 = 0, 0xFFFFFFFF
        self.out = bytearray()

    def encode(self, bit: int, p: int) -> None:
        xmid = split(self.x1, self.x2, p)
        if bit:
            self.x2 = xmid
        else:
            self.x1 = xmid + 1
        while ((self.x1 ^ self.x2) & 0xFF000000) == 0:
            self.out.append(self.x2 >> 24)
            self.x1 = (self.x1 << 8) & 0xFFFFFFFF
            self.x2 = ((self.x2 << 8) & 0xFFFFFFFF) | 0xFF

    def finish(self) -> bytes:
        # Emitting all four bytes of x1 pins the decoder's x inside [x1, x2].
        self.out += self.x1.to_bytes(4, "big")
        return bytes(self.out)


class Decoder:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0
        self.x1, self.x2 = 0, 0xFFFFFFFF
        self.x = 0
        for _ in range(4):
            self.x = (self.x << 8) | self._byte()

    def _byte(self) -> int:
        if self.pos < len(self.data):
            b = self.data[self.pos]
            self.pos += 1
            return b
        return 0

    def decode(self, p: int) -> int:
        xmid = split(self.x1, self.x2, p)
        if self.x <= xmid:
            bit, self.x2 = 1, xmid
        else:
            bit, self.x1 = 0, xmid + 1
        while ((self.x1 ^ self.x2) & 0xFF000000) == 0:
            self.x1 = (self.x1 << 8) & 0xFFFFFFFF
            self.x2 = ((self.x2 << 8) & 0xFFFFFFFF) | 0xFF
            self.x = ((self.x << 8) & 0xFFFFFFFF) | self._byte()
        return bit


class Order2Model:
    """Direct-indexed order-2 context model: 65536 two-byte contexts x 256 partial bytes."""

    def __init__(self) -> None:
        self.p = array("H", [PROB_ONE // 2]) * (1 << 24)
        self.n = bytearray(1 << 24)
        self.ctx = 0  # previous two bytes << 8
        self.c0 = 1

    def predict(self) -> int:
        p = self.p[self.ctx | self.c0]
        return min(max(p, 32), PROB_ONE - 32)  # never 0 or 1: a miss must stay codable

    def update(self, bit: int) -> None:
        i = self.ctx | self.c0
        n = self.n[i]
        self.p[i] += ((bit << 16) - self.p[i]) * DT[n] >> 16
        if n < LIMIT:
            self.n[i] = n + 1
        self.c0 = (self.c0 << 1) | bit
        if self.c0 >= 256:
            byte = self.c0 & 0xFF
            self.ctx = ((self.ctx << 8) | (byte << 8)) & 0xFFFF00
            self.c0 = 1


def compress(data: bytes) -> bytes:
    """Code ``data`` with the order-2 model.  The length is not stored (caller keeps it)."""
    enc, model = Encoder(), Order2Model()
    for byte in data:
        for k in range(7, -1, -1):
            bit = (byte >> k) & 1
            enc.encode(bit, model.predict())
            model.update(bit)
    return enc.finish()


def decompress(payload: bytes, n: int) -> bytes:
    dec, model = Decoder(payload), Order2Model()
    out = bytearray(n)
    for i in range(n):
        c = 0
        for _ in range(8):
            bit = dec.decode(model.predict())
            model.update(bit)
            c = (c << 1) | bit
        out[i] = c
    return bytes(out)
