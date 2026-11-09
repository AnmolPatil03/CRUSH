r"""Fixed-point logistic tables shared by the mixer, the APM/SSE stages and the coder.

Definitions (real-valued):

    squash(x)  = 1 / (1 + e^{-x})          logistic function
    stretch(p) = ln(p / (1 - p))            its inverse (the logit)

Integer representation used everywhere in CRUSH:

* probabilities are 16-bit: ``P = round(65536 * p)``, clipped to ``[22, 65514]``;
* the stretched domain is scaled by 256 and clipped to ``[-2047, 2047]``
  (``x = 256 * ln(p/(1-p))``, i.e. +-8 nats).

``squash`` is evaluated by linear interpolation between 33 knots spaced 128
apart (one knot per half-nat).  The knots are integer constants written out
below, and ``stretch`` is derived from ``squash`` by exact integer inversion, so
the tables are bit-identical on every platform (no libm calls), which keeps
archives portable between Windows, Linux and macOS.
"""

from __future__ import annotations

import numpy as np

# round(65536 / (1 + exp(-(k - 16) / 2))) for k = 0..32, clipped to [1, 65535].
_KNOTS = (22, 36, 60, 98, 162, 267, 439, 720, 1179, 1921, 3108, 4971, 7812, 11955,
          17625, 24743, 32768, 40793, 47911, 53581, 57724, 60565, 62428, 63615,
          64357, 64816, 65097, 65269, 65374, 65438, 65476, 65500, 65514)


def _build_squash() -> np.ndarray:
    """SQUASH[x + 2047] = 16-bit probability for stretched value x in [-2047, 2047]."""
    t = np.zeros(4095, dtype=np.int64)
    for x in range(-2047, 2048):
        w = x & 127
        k = (x >> 7) + 16
        t[x + 2047] = (_KNOTS[k] * (128 - w) + _KNOTS[k + 1] * w + 64) >> 7
    return t


def _build_stretch(sq: np.ndarray) -> np.ndarray:
    """STRETCH[p >> 4] = smallest x with squash(x) >= p (12-bit index, exact inverse)."""
    t = np.zeros(4096, dtype=np.int64)
    pi = 0
    for x in range(-2047, 2048):
        v = int(sq[x + 2047]) >> 4
        for j in range(pi, v + 1):
            t[j] = x
        pi = max(pi, v + 1)
    t[pi:] = 2047
    return t


SQUASH = _build_squash()
STRETCH = _build_stretch(SQUASH)
