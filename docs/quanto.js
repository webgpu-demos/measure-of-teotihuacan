// Cosine quantogram (Kendall 1974) with a Monte Carlo noise threshold.
// Shared by the film's Step 5 scene and the app's interactive explorer.

export function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gauss(rand) {
  const u = Math.max(rand(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * rand());
}

// Synthetic building dimensions in metres. `unit` null means no unit at all.
// `share` is the fraction of lengths laid out in whole units; the rest are
// incidental lengths (erosion, later fills, things nobody set out).
export function synth({ unit = 0.83, n = 120, sigma = 0.02, share = 0.7, seed = 7 } = {}) {
  const rand = rng(seed);
  const lo = Math.log(2), hi = Math.log(36);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    if (unit && rand() < share) {
      const k = Math.round(Math.exp(lo + (hi - lo) * rand()));
      out[i] = k * unit + sigma * gauss(rand);
    } else {
      out[i] = Math.exp(Math.log(1.6) + (Math.log(30) - Math.log(1.6)) * rand());
    }
  }
  return out;
}

export function phi(xs, q) {
  let s = 0;
  const w = (2 * Math.PI) / q;
  for (let i = 0; i < xs.length; i++) s += Math.cos(w * xs[i]);
  return Math.sqrt(2 / xs.length) * s;
}

export function grid(qmin = 0.3, qmax = 2.0, step = 0.0025) {
  const n = Math.round((qmax - qmin) / step) + 1;
  const qs = new Float64Array(n);
  for (let i = 0; i < n; i++) qs[i] = qmin + i * step;
  return qs;
}

export function scan(xs, qs) {
  const ys = new Float64Array(qs.length);
  for (let i = 0; i < qs.length; i++) ys[i] = phi(xs, qs[i]);
  return ys;
}

// Kendall's simulation: perturb each length by up to ±15% so any shared unit is
// destroyed but the spread of sizes is kept; record the highest score anywhere
// in the scan. The 95th and 99th percentiles are the bars a real peak must clear.
export function threshold(xs, qs, { sims = 160, jitter = 0.15, seed = 99 } = {}) {
  const rand = rng(seed);
  const maxes = new Float64Array(sims);
  const tmp = new Float64Array(xs.length);
  const coarse = qs.filter((_, i) => i % 2 === 0);
  for (let s = 0; s < sims; s++) {
    for (let i = 0; i < xs.length; i++) tmp[i] = xs[i] * (1 + jitter * (2 * rand() - 1));
    let m = -Infinity;
    for (let j = 0; j < coarse.length; j++) m = Math.max(m, phi(tmp, coarse[j]));
    maxes[s] = m;
  }
  const sorted = Array.from(maxes).sort((a, b) => a - b);
  const pick = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  return { p95: pick(0.95), p99: pick(0.99) };
}

export function peak(qs, ys) {
  let best = 0;
  for (let i = 1; i < ys.length; i++) if (ys[i] > ys[best]) best = i;
  return { q: qs[best], phi: ys[best], index: best };
}

export function valueAt(qs, ys, q) {
  const step = qs[1] - qs[0];
  const i = Math.max(0, Math.min(qs.length - 1, Math.round((q - qs[0]) / step)));
  return ys[i];
}
