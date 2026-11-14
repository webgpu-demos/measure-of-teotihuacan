"""Cosine quantogram (Kendall 1974) with Kendall's Monte Carlo noise bar.

Candidate units are spaced evenly in 1/q, finely enough that the longest length's phase moves at most
a quarter radian per step; an even grid in q undersamples long lengths at small q and misses peaks.
The noise bar scans the same grid it is compared against.

Caution, measured with cadmeasure.py selftest: the noise bar assumes every length is independent.
Lengths read off drawings are not (a few design choices drive many measured lengths), and on made-up
drawings with no unit the 1% bar is crossed in roughly one set in eight.
"""
import numpy as np


def grid(qmin=0.5, qmax=2.0, xmax=40.0, phase=0.25):
    dt = phase / (2 * np.pi * xmax)
    return np.sort(1 / np.arange(1 / qmax, 1 / qmin + dt / 2, dt))


def scan(xs, qs):
    xs = np.asarray(xs, dtype=float)
    return np.sqrt(2 / len(xs)) * np.cos(2 * np.pi * xs[None, :] / np.asarray(qs)[:, None]).sum(axis=1)


def peak(qs, ys):
    i = int(np.argmax(ys))
    return {'q': float(qs[i]), 'phi': float(ys[i]), 'index': i}


def threshold(xs, qs, sims=200, jitter=0.15, seed=99):
    """Perturb every length by up to +/-15% (destroys any unit, keeps the spread of sizes) and record the
    highest score anywhere in the scan. The 95th and 99th percentiles are the bars a real peak must clear."""
    rng = np.random.default_rng(seed)
    xs = np.asarray(xs, dtype=float)
    maxes = [scan(xs * (1 + jitter * rng.uniform(-1, 1, len(xs))), qs).max() for _ in range(sims)]
    return {'p95': float(np.quantile(maxes, 0.95)), 'p99': float(np.quantile(maxes, 0.99))}
