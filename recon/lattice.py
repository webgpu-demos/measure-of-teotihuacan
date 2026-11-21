"""City-scale spacing pilot: do grid-aligned edges across Teotihuacan sit at shared spacings?

Nobody picks lines. Every straight edge in the public INEGI 1.5 m terrain model that runs within 3 degrees
of the city grid (15.5 degrees east of north) is extracted automatically, and every spacing is scored at once:

  local test   every pair of parallel edges that face each other, 5-600 m apart: Kendall's cosine
               quantogram of their separations, 6-60 m. Do separations favour multiples of q?
  global test  the periodogram of all edge positions along each grid axis, 6-300 m. Do edges across the
               whole city sit on one ruler of spacing q, with one origin?

Both curves are measured against their own running median (+-10% in q), the local one also in units of its
running spread, so broad bumps from common building sizes or uneven edge density do not count; a shared
spacing gives sharp peaks.

The chance bar comes from fake versions of the same edges: cut into 300 m tiles, each tile stretched by up to
+-15% and shifted at random. Each tile keeps its own shapes, but any spacing shared between tiles is broken.
The tallest peak anywhere in each fake scan sets the 95% and 99% bars, which pays for scanning many spacings.
The spacings in TARGETS were written down before the real data was scanned; for those, the pointwise p-value
is reported as well. Decision rule, also fixed in advance: a spacing counts as found only if it clears the 99%
bar for the whole box and also reaches p < 0.05 at the same spacing in the west and east halves, scanned
separately. On 20 synthetic cities with no unit, the 95% and 99% bars were crossed at about their nominal rates.

Before the real data, the same code runs on synthetic cities with a known answer (SYNTH).

The 1.5 m terrain cannot resolve 83 cm itself. It can resolve spacings from about 8 m up, which is where the
base-10, base-20 and 260 claims live. Which subdivision is the unit has to come from the excavation CAD.

Usage (Python with numpy, scipy, matplotlib; GDAL command-line tools; inputs from recon/build_data.py):
  python3 -I recon/lattice.py synth     # validate on synthetic cities -> recon/pilot/synth.json
  python3 -I recon/lattice.py real      # the public terrain model     -> recon/pilot/real.json
  python3 -I recon/lattice.py power     # how strong a ruler it could catch -> recon/pilot/power.json
  python3 -I recon/lattice.py figures   # figures from the saved runs  -> recon/pilot/*.png
"""
import json
import math
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from scipy import ndimage

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'recon-data')
WORK = os.path.join(RAW, 'lattice')
OUT = os.path.join(ROOT, 'recon', 'pilot')
SITES = os.path.join(ROOT, 'docs', 'reconstruction', 'data', 'sites.json')
GDAL = os.path.dirname(shutil.which('gdalwarp') or '/opt/anaconda3/bin/gdalwarp')

ORIGIN = (516200.0, 2177150.0)          # local frame of build_data.py: metres east and north of this UTM 14N point
SRC_X0, SRC_Y1, SRC_RES = 511611.0, 2183862.0, 1.5   # INEGI sheets E14B21B1-B4 pixel grid
GRID_DEG = 15.5
CA, SA = math.cos(math.radians(GRID_DEG)), math.sin(math.radians(GRID_DEG))
STEP = 1.5                              # grid-frame raster spacing (m)
CENTRE = (-130.0, -300.0)               # study box centre in grid axes (u, v): on the Street of the Dead
HALF = 3000.0                           # study box is 6 x 6 km
TILE = 300.0
# Edge thresholds. Run 1 used gmin 0.10, max_rms 0.6, min_len 15 (tuned on synthetic platforms) and kept only 850 edges
# in 36 km2; these were loosened after looking at edge yield and maps only, before any second scan.
EDGE = dict(gmin=0.04, max_rms=0.8, min_len=12.0)

# Written down before the real data was scanned.
TARGETS = [
    (8.30, '10 TMU'), (10.79, '13 TMU'), (16.60, '20 TMU'), (43.16, '52 TMU'), (215.8, '260 TMU'),
    (16.10, '20 x 80.5 cm'), (8.38, '10 varas'), (16.76, '20 varas'), (10.0, '10 m'), (20.0, '20 m'),
]
BASE20 = [(16.10, '20 x 80.5 cm'), (16.60, '20 TMU'), (16.76, '20 varas'), (20.0, '20 m')]

SYNTH = {   # planted spacing (m); 'local' shares 16.6 m inside 600 m neighbourhoods but not between them
    'tmu20': 16.60, 'alt805': 16.10, 'vara20': 16.76, 'metre20': 20.0, 'local': 16.60, 'none': None,
}

QS_LOCAL = np.exp(np.arange(math.log(6.0), math.log(60.0), 0.001))
QS_GLOBAL = np.exp(np.arange(math.log(6.0), math.log(300.0), 0.001))
MED = 191                               # running-median window: +-10% in q at 0.1% steps


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# ------------------------------------------------------------------ rasters in the grid frame
def frame(cu, cv, half):
    n = int(round(2 * half / STEP))
    return {'n': n, 'cu': cu, 'cv': cv, 'u_left': cu - half, 'v_top': cv + half,
            'us': cu - half + (np.arange(n) + 0.5) * STEP, 'vs': cv + half - (np.arange(n) + 0.5) * STEP}


def read_real(fr):
    """DTM and DSM around the study box, cut on the source pixel grid (no resampling), in local metres."""
    cx, cy = fr['cu'] * CA + fr['cv'] * SA, -fr['cu'] * SA + fr['cv'] * CA
    ext = (fr['n'] * STEP / 2) * (CA + SA) + 10
    snap = lambda v, o: o + SRC_RES * math.floor((v - o) / SRC_RES)
    xmin, ymax = snap(ORIGIN[0] + cx - ext, SRC_X0), snap(ORIGIN[1] + cy + ext, SRC_Y1) + SRC_RES
    w = h = int(math.ceil(2 * ext / SRC_RES)) + 1
    box = {'x0': xmin - ORIGIN[0], 'y1': ymax - ORIGIN[1], 'res': SRC_RES, 'w': w, 'h': h}
    os.makedirs(WORK, exist_ok=True)
    out = []
    for name in ('dtm', 'dsm'):
        tif, bil = os.path.join(WORK, f'{name}.tif'), os.path.join(WORK, f'{name}.bil')
        subprocess.run([os.path.join(GDAL, 'gdalwarp'), '-q', '-overwrite', '-te', str(xmin), str(ymax - h * SRC_RES),
                        str(xmin + w * SRC_RES), str(ymax), '-tr', str(SRC_RES), str(SRC_RES), '-r', 'near',
                        os.path.join(RAW, f'{name}.vrt'), tif], check=True)
        subprocess.run([os.path.join(GDAL, 'gdal_translate'), '-q', '-of', 'ENVI', '-ot', 'Float32', tif, bil], check=True)
        a = np.fromfile(bil, dtype=np.float32).reshape(h, w)
        a[~np.isfinite(a) | (a < 1000)] = np.nan
        out.append(a)
    return out[0], out[1], box


def rotate(a, box, fr):
    """Sample a north-up raster (local metres) onto the grid frame: rows run down v, columns along u."""
    good = np.isfinite(a)
    filled = np.where(good, a, 0).astype(np.float32)
    good = good.astype(np.float32)
    n = fr['n']
    out, ok = np.empty((n, n), np.float32), np.empty((n, n), bool)
    U = fr['us'][None, :]
    for r0 in range(0, n, 400):
        V = fr['vs'][r0:r0 + 400, None]
        X, Y = U * CA + V * SA, -U * SA + V * CA
        rc = [(box['y1'] - Y) / box['res'] - 0.5, (X - box['x0']) / box['res'] - 0.5]
        out[r0:r0 + 400] = ndimage.map_coordinates(filled, rc, order=1, cval=0)
        ok[r0:r0 + 400] = ndimage.map_coordinates(good, rc, order=1, cval=0) > 0.999
    return out, ok


# ------------------------------------------------------------------ edges
def lines_down_rows(across, along, ok, min_len=15.0, gmin=0.10, tilt_deg=3.0, max_rms=0.6):
    """Straight edges that run down the rows (near-constant column). Returns sub-pixel column, row span, strength."""
    m = np.abs(across)
    left, right = np.roll(m, 1, axis=1), np.roll(m, -1, axis=1)
    cand = ok & (m > gmin) & (m > 2 * np.abs(along)) & (m >= left) & (m > right)
    L = max(3, int(round(min_len / STEP)))
    band = ndimage.binary_dilation(cand, structure=np.ones((1, 3), bool))
    keep = cand & ndimage.binary_opening(band, structure=np.ones((L, 1), bool))
    lab, n = ndimage.label(keep, structure=np.ones((3, 3), int))
    rr, cc = np.nonzero(keep)
    ids = lab[rr, cc]
    ml, mc, mr = left[rr, cc], m[rr, cc], right[rr, cc]
    den = ml - 2 * mc + mr
    off = np.clip(np.where(den < 0, 0.5 * (ml - mr) / np.where(den < 0, den, -1.0), 0.0), -0.5, 0.5)
    p, r, w = cc + off, rr.astype(np.float64), mc.astype(np.float64)
    S = lambda x: np.bincount(ids, x, minlength=n + 1)[1:]
    sw, sr, sp, srr, srp, spp, cnt = S(w), S(w * r), S(w * p), S(w * r * r), S(w * r * p), S(w * p * p), S(np.ones_like(w))
    r0 = np.full(n + 1, np.inf); np.minimum.at(r0, ids, r); r0 = r0[1:]
    r1 = np.full(n + 1, -np.inf); np.maximum.at(r1, ids, r); r1 = r1[1:]
    with np.errstate(invalid='ignore', divide='ignore'):
        slope = (sw * srp - sr * sp) / (sw * srr - sr * sr)
        icpt = (sp - slope * sr) / sw
        res = (spp - 2 * icpt * sp - 2 * slope * srp + icpt ** 2 * sw + 2 * icpt * slope * sr + slope ** 2 * srr) / sw
    span = r1 - r0 + 1
    good = ((span >= L) & (np.abs(slope) <= math.tan(math.radians(tilt_deg))) & (np.sqrt(np.maximum(res, 0)) <= max_rms)
            & (cnt >= 0.8 * span) & np.isfinite(slope))
    mid = (r0 + r1) / 2
    return (icpt + slope * mid)[good], r0[good], r1[good], (sw / cnt)[good]


def extract(z, ok, fr, **kw):
    """All grid-aligned edges: [N-S running (position u), E-W running (position v)]."""
    zs = ndimage.gaussian_filter(z, 1.0)
    lr = zs - ndimage.gaussian_filter(zs, 20.0, truncate=3.0)      # local relief: removes hillside slope
    gu = np.zeros_like(lr); gu[:, 1:-1] = (lr[:, 2:] - lr[:, :-2]) / (2 * STEP)
    gv = np.zeros_like(lr); gv[1:-1, :] = (lr[2:, :] - lr[:-2, :]) / (2 * STEP)
    ok = ndimage.binary_erosion(ok, iterations=1)
    segs = []
    p, r0, r1, s = lines_down_rows(gu, gv, ok, **kw)
    pos = fr['u_left'] + (p + 0.5) * STEP
    a0, a1 = fr['v_top'] - (r1 + 0.5) * STEP, fr['v_top'] - (r0 + 0.5) * STEP
    segs.append({'axis': 'u', 'pos': pos, 'a0': a0, 'a1': a1, 'mu': pos, 'mv': (a0 + a1) / 2, 'strength': s})
    p, r0, r1, s = lines_down_rows(np.ascontiguousarray(gv.T), np.ascontiguousarray(gu.T), np.ascontiguousarray(ok.T), **kw)
    pos = fr['v_top'] - (p + 0.5) * STEP
    a0, a1 = fr['u_left'] + (r0 + 0.5) * STEP, fr['u_left'] + (r1 + 0.5) * STEP
    segs.append({'axis': 'v', 'pos': pos, 'a0': a0, 'a1': a1, 'mu': (a0 + a1) / 2, 'mv': pos, 'strength': s})
    return segs


def subset(segs, keep):
    out = []
    for s in segs:
        k = keep(s['mu'], s['mv'])
        out.append({key: (v[k] if isinstance(v, np.ndarray) else v) for key, v in s.items()})
    return out


def tiles(s):
    tu, tv = np.floor(s['mu'] / TILE), np.floor(s['mv'] / TILE)
    _, tid = np.unique(tu * 100003 + tv, return_inverse=True)
    ctr = ((tu if s['axis'] == 'u' else tv) + 0.5) * TILE       # tile centre along the position axis
    return tid, ctr


# ------------------------------------------------------------------ local test: facing pairs
DBIN, NB = 0.1, 6500
_DB = (np.arange(NB) + 0.5) * DBIN
_WB = np.ones(NB)
_WB[_DB < 10] = 0
_WB[_DB > 600] = 0
_m = (_DB >= 10) & (_DB < 25); _WB[_m] = np.sin(0.5 * np.pi * (_DB[_m] - 10) / 15) ** 2
_m = (_DB > 450) & (_DB <= 600); _WB[_m] = np.cos(0.5 * np.pi * (_DB[_m] - 450) / 150) ** 2
_COS = None


def phi(h):
    global _COS
    if _COS is None:
        _COS = (np.cos(2 * np.pi * _DB[None, :] / QS_LOCAL[:, None]) * _WB).astype(np.float32)
    N = float(np.sum(h * _WB ** 2))
    return np.sqrt(2 / max(N, 1)) * (_COS @ h.astype(np.float32))


def facing_pairs(pos, a0, a1, dmin=5.0, dmax=600.0, chunk=5_000_000):
    o = np.argsort(pos)
    p, lo, hi = pos[o], a0[o], a1[o]
    cnt = np.searchsorted(p, p + dmax, side='right') - np.arange(len(p)) - 1
    Is, Js, i = [], [], 0
    cum = np.cumsum(cnt)
    while i < len(p):
        base = cum[i - 1] if i else 0
        k = int(np.searchsorted(cum, base + chunk, side='right'))
        rows = np.arange(i, max(k, i + 1))
        c = cnt[rows]
        I = np.repeat(rows, c)
        J = I + 1 + (np.arange(c.sum()) - np.repeat(np.cumsum(c) - c, c))
        ov = np.minimum(hi[I], hi[J]) - np.maximum(lo[I], lo[J])
        m = (ov > 0) & (p[J] - p[I] >= dmin)
        Is.append(o[I[m]]); Js.append(o[J[m]])
        i = rows[-1] + 1
    return np.concatenate(Is).astype(np.int32), np.concatenate(Js).astype(np.int32)


def local_test(segs, reps=200, seed=1):
    rng = np.random.default_rng(seed)
    prep = []
    for s in segs:
        I, J = facing_pairs(s['pos'], s['a0'], s['a1'])
        tid, ctr = tiles(s)
        prep.append((s['pos'], I, J, tid, ctr))

    def hist(ps):
        h = np.zeros(NB)
        for (_, I, J, _, _), p in zip(prep, ps):
            b = (np.abs(p[J] - p[I]) / DBIN).astype(np.int64)
            h += np.bincount(b[b < NB], minlength=NB)
        return h

    h = hist([x[0] for x in prep])
    real = phi(h)
    null = np.empty((reps, len(QS_LOCAL)), np.float32)
    for r in range(reps):
        ps = []
        for pos, _, _, tid, ctr in prep:
            nt = tid.max() + 1 if len(tid) else 1
            sc, sh = rng.uniform(0.85, 1.15, nt), rng.uniform(-50, 50, nt)
            ps.append(ctr + sc[tid] * (pos - ctr) + sh[tid])
        null[r] = phi(hist(ps))
    return {'q': QS_LOCAL, 'raw': real, 'real': zscore(real), 'null': np.array([zscore(y) for y in null]),
            'hist': h, 'npairs': int(sum(len(x[1]) for x in prep))}


def zscore(y):
    """Height above the running median (+-10% in q) in units of the running spread (+-30%): each scan, real or
    fake, is measured against its own noise level, so the bar does not depend on modelling that noise exactly."""
    d = y - ndimage.median_filter(y, size=MED, mode='reflect')
    s = 1.4826 * ndimage.median_filter(np.abs(d), size=3 * MED, mode='reflect')
    return d / np.maximum(s, 0.5 * np.median(s) + 1e-9)            # floor: a nearly flat stretch is not zero noise


def comb(h, q, kmax=None):
    """Mean cosine of separations near each multiple k*q: a shared unit lifts many k, a common size only one."""
    out = []
    for k in range(1, int(600 / q) + 1):
        m = (_DB >= (k - 0.5) * q) & (_DB < (k + 0.5) * q)
        wsum = np.sum(h[m] * _WB[m])
        if wsum > 20:
            out.append(round(float(np.sum(h[m] * _WB[m] * np.cos(2 * np.pi * _DB[m] / q)) / wsum), 3))
    return out


# ------------------------------------------------------------------ global test: one ruler city-wide
GB, NFFT, SPAN = 0.25, 2 ** 18, 5000.0
_F = np.fft.rfftfreq(NFFT, GB)
_qd = QS_GLOBAL[::-1]                                         # descending q = ascending frequency
_fb = 1 / np.sqrt(_qd[:-1] * _qd[1:])
_IDX = np.searchsorted(_F, np.concatenate([[1 / (_qd[0] * 1.0005)], _fb, [1 / (_qd[-1] / 1.0005)]]))


def spectrum(ps):
    S = np.zeros(len(_F))
    for p in ps:
        b = ((p + SPAN) / GB).astype(np.int64)
        h = np.bincount(b[(b >= 0) & (b < 2 * SPAN / GB)], minlength=int(2 * SPAN / GB))
        F = np.fft.rfft(h, NFFT)
        S += (F.real ** 2 + F.imag ** 2) / max(len(p), 1)
    return S


def pool(S):
    return np.maximum.reduceat(S[:_IDX[-1]], _IDX[:-1])[::-1]    # max over the FFT bins inside each q step


def global_test(segs, centre, reps=300, seed=2):
    rng = np.random.default_rng(seed)
    rel = [s['pos'] - centre[0 if s['axis'] == 'u' else 1] for s in segs]
    tids = [tiles(s)[0] for s in segs]
    flat = lambda y: y / ndimage.median_filter(y, size=MED, mode='reflect')
    S = spectrum(rel)
    null = np.empty((reps, len(QS_GLOBAL)), np.float32)
    for r in range(reps):
        null[r] = flat(pool(spectrum([p + rng.uniform(-1000, 1000, t.max() + 1 if len(t) else 1)[t] for p, t in zip(rel, tids)])))
    return {'q': QS_GLOBAL, 'raw': pool(S), 'real': flat(pool(S)), 'null': null, 'S': S, 'rel': rel, 'tids': tids}


def locate(rel, tids, q_lo, q_hi, boots=200, seed=3):
    """Strongest sharp peak between q_lo and q_hi, refined on the fine FFT grid, with a tile bootstrap interval."""
    rng = np.random.default_rng(seed)
    sel = np.nonzero((_F >= 1 / q_hi) & (_F <= 1 / q_lo))[0]

    def peak(S):
        i = sel[np.argmax(S[sel])]
        y0, y1, y2 = S[i - 1], S[i], S[i + 1]
        d = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) if (y0 - 2 * y1 + y2) < 0 else 0.0
        return 1 / (_F[i] + d * (_F[1] - _F[0]))

    q0 = peak(spectrum(rel))
    boot = []
    for _ in range(boots):
        ps = []
        for p, t in zip(rel, tids):
            nt = t.max() + 1 if len(t) else 1
            pick = rng.integers(0, nt, nt)
            w = np.bincount(pick, minlength=nt)[t]                  # how often each segment's tile was drawn
            ps.append(np.repeat(p, w))
        boot.append(peak(spectrum(ps)))
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return {'q': round(float(q0), 3), 'ci95': [round(float(lo), 3), round(float(hi), 3)]}


# ------------------------------------------------------------------ summarising a scan
def summarise(t, kind):
    q, real, null = t['q'], t['real'], t['null']
    mx = null.max(axis=1)
    b95, b99 = float(np.percentile(mx, 95)), float(np.percentile(mx, 99))
    pk = np.nonzero((real[1:-1] > real[:-2]) & (real[1:-1] >= real[2:]) & (real[1:-1] > b95))[0] + 1
    pk = sorted(pk, key=lambda i: -real[i])[:8]
    tg = []
    for qt, name in TARGETS:
        if q[0] * 1.004 <= qt <= q[-1] / 1.004:
            v, p = p_at(t, qt)
            tg.append({'name': name, 'q': qt, 'score': v, 'p': p})
    out = {'bar95': round(b95, 3), 'bar99': round(b99, 3), 'max': round(float(real.max()), 3),
           'q_at_max': round(float(q[np.argmax(real)]), 3),
           'peaks': [{'q': round(float(q[i]), 3), 'score': round(float(real[i]), 3), 'over_bar99': bool(real[i] > b99)} for i in pk],
           'targets': tg}
    sig = [p for p in out['peaks'] if p['over_bar99']]
    if kind == 'local':
        # The true spacing lifts every multiple; its echoes (q/2, q/3) lift only every 2nd or 3rd, and side lobes drift.
        out['npairs'] = t['npairs']
        for p in sig:
            c = comb(t['hist'], p['q'])
            p['comb_mean'], p['comb'] = round(float(np.mean(c)), 3), c[:12]
        best = max(sig, key=lambda p: p['comb_mean'], default=None)
    else:
        # A ruler of spacing L also peaks at L/2, L/3 but not at 2L: the longest major peak is the spacing.
        best = max([p for p in sig if p['score'] >= 0.15 * out['max']], key=lambda p: p['q'], default=None)
    out['fundamental'] = best['q'] if best else None
    return out


def p_at(t, qt):
    """Score and pointwise p-value at one spacing named in advance (best value within +-0.4%)."""
    w = (t['q'] >= qt / 1.004) & (t['q'] <= qt * 1.004)
    v = float(t['real'][w].max())
    return round(v, 3), round(float(np.mean(t['null'][:, w].max(axis=1) >= v)), 4)


def analyse(segs, centre, reps_local=200, reps_global=300, locate_unit=True):
    t0 = time.time()
    res = {'segments': [int(len(s['pos'])) for s in segs]}
    lt = local_test(segs, reps_local)
    gt = global_test(segs, centre, reps_global)
    res['local'], res['global'] = summarise(lt, 'local'), summarise(gt, 'global')
    if locate_unit:
        res['global']['base20_peak'] = locate(gt['rel'], gt['tids'], 15.6, 20.6)
    res['seconds'] = round(time.time() - t0, 1)
    curves = {'lq': lt['q'], 'lreal': lt['real'], 'lraw': lt['raw'], 'lnull95': np.percentile(lt['null'].max(axis=1), 95),
              'lnull99': np.percentile(lt['null'].max(axis=1), 99), 'gq': gt['q'], 'greal': gt['real'],
              'gnull95': np.percentile(gt['null'].max(axis=1), 95), 'gnull99': np.percentile(gt['null'].max(axis=1), 99)}
    return res, curves, {'local': lt, 'global': gt}


# ------------------------------------------------------------------ synthetic cities
def synth_city(kind, seed=11, half=2000.0):
    """A 4 x 4 km terrain at 1.5 m with eroded compounds and modern field ridges, north-up like the INEGI tiles."""
    rng = np.random.default_rng(seed)
    L = SYNTH[kind]
    ext = half * (CA + SA) + 30
    n = int(2 * ext / SRC_RES)
    box = {'x0': -ext, 'y1': ext, 'res': SRC_RES, 'w': n, 'h': n}
    xs = (box['x0'] + (np.arange(n) + 0.5) * SRC_RES).astype(np.float32)
    ys = (box['y1'] - (np.arange(n) + 0.5) * SRC_RES).astype(np.float32)
    UU = xs[None, :] * CA - ys[:, None] * SA
    VV = xs[None, :] * SA + ys[:, None] * CA
    plat, ridge = np.zeros((n, n), np.float32), np.zeros((n, n), np.float32)

    def paint(layer, u0, u1, v0, v1, h):
        cx = [u * CA + v * SA for u in (u0, u1) for v in (v0, v1)]
        cy = [-u * SA + v * CA for u in (u0, u1) for v in (v0, v1)]
        c0, c1 = max(0, int((min(cx) - box['x0']) / SRC_RES)), min(n, int((max(cx) - box['x0']) / SRC_RES) + 2)
        r0, r1 = max(0, int((box['y1'] - max(cy)) / SRC_RES)), min(n, int((box['y1'] - min(cy)) / SRC_RES) + 2)
        if c1 <= c0 or r1 <= r0:
            return
        U, V = UU[r0:r1, c0:c1], VV[r0:r1, c0:c1]
        layer[r0:r1, c0:c1] += h * ((U >= u0) & (U <= u1) & (V >= v0) & (V <= v1))

    hoods = {}
    for _ in range(500):
        planned = L is not None and rng.random() < 0.6
        if planned:
            nu, nv = rng.integers(2, 7, 2)
            u0, v0 = (L * np.round(rng.uniform(-half + 60, half - 180, 2) / L))
            if kind == 'local':
                key = (int((u0 + half) // 600), int((v0 + half) // 600))
                hoods.setdefault(key, rng.uniform(0, L, 2))
                u0, v0 = u0 + hoods[key][0], v0 + hoods[key][1]
            w, d = nu * L, nv * L
            court = (u0 + L, u0 + w - L, v0 + L, v0 + d - L) if nu > 2 and nv > 2 else None
        else:
            u0, v0 = rng.uniform(-half + 60, half - 180, 2)
            w, d = rng.uniform(33, 115, 2)
            i = rng.uniform(10, 22)
            court = (u0 + i, u0 + w - i, v0 + i, v0 + d - i) if min(w, d) > 55 else None
        e = rng.normal(0, 0.5, 8)                                   # builders' error on each edge
        h = rng.uniform(0.6, 2.0)
        paint(plat, u0 + e[0], u0 + w + e[1], v0 + e[2], v0 + d + e[3], h)
        if court:
            paint(plat, court[0] + e[4], court[1] + e[5], court[2] + e[6], court[3] + e[7], -0.5 * h)
    for axis in (0, 1):                                            # modern field ridges along the grid, random spacing
        x = -half
        while x < half:
            x += rng.uniform(25, 140)
            a = -half + rng.uniform(0, 200)
            while a < half:
                ln = rng.uniform(80, 500)
                if rng.random() < 0.5:
                    paint(ridge, *((x - 1, x + 1, a, a + ln) if axis == 0 else (a, a + ln, x - 1, x + 1)), 0.35)
                a += ln + rng.uniform(0, 200)
    del UU, VV
    coarse = ndimage.gaussian_filter(rng.normal(0, 1, (n // 60 + 2, n // 60 + 2)), 1.0)
    terrain = ndimage.zoom(coarse, n / coarse.shape[0], order=3)[:n, :n] * 6.0
    z = (2300 + 0.015 * ys[:, None] + terrain + ndimage.gaussian_filter(plat, 1.5)
         + ndimage.gaussian_filter(ridge, 0.7) + rng.normal(0, 0.04, (n, n))).astype(np.float32)
    return z, box


# ------------------------------------------------------------------ commands
def cmd_synth():
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORK, exist_ok=True)
    results, curves = {}, {}
    for kind in SYNTH:
        log('synthetic city', kind)
        z, box = synth_city(kind)
        fr = frame(0.0, 0.0, 2000.0)
        zr, ok = rotate(z, box, fr)
        del z
        segs = extract(zr, ok, fr, **EDGE)
        res, cv, _ = analyse(segs, (0.0, 0.0))
        res['planted'] = SYNTH[kind]
        results[kind] = res
        curves.update({f'{kind}_{k}': v for k, v in cv.items()})
        g, l = res['global'], res['local']
        log(f"  segments {res['segments']}  pairs {l['npairs']}  local max {l['max']} at {l['q_at_max']} (bar99 {l['bar99']})"
            f"  global max {g['max']} at {g['q_at_max']} (bar99 {g['bar99']})  base-20 peak {g['base20_peak']}  {res['seconds']}s")
    json.dump(results, open(os.path.join(OUT, 'synth.json'), 'w'), indent=1)
    np.savez_compressed(os.path.join(WORK, 'synth_curves.npz'), **curves)


def zone_mask():
    s = json.load(open(SITES))
    from matplotlib.path import Path
    to_grid = lambda pts: np.array([(x * CA - y * SA, x * SA + y * CA) for x, y in pts])
    zone = Path(to_grid(s['sites']['zone']['poly']))
    mon = [Path(to_grid(s['sites'][k]['poly'])) for k in ('sun', 'moon', 'fsp', 'fsp-adosada', 'ciudadela-wall', 'ciudadela-platform')
           if k in s['sites']]
    st = s['derived']['street']
    cit = s['derived']['ciudadela']
    cu, cv = cit['cx'] * CA - cit['cy'] * SA, cit['cx'] * SA + cit['cy'] * CA

    def inside(path, mu, mv, buf=0.0):
        return path.contains_points(np.c_[mu, mv], radius=buf)

    def monuments(mu, mv):
        m = np.zeros(len(mu), bool)
        for p in mon:
            m |= inside(p, mu, mv, 40.0) | inside(p, mu, mv, -40.0)
        m |= (np.abs(mu - st['u']) < 80) & (mv > st['v0'] - 50) & (mv < st['v1'] + 50)
        m |= (np.abs(mu - cu) < cit['w'] / 2 + 40) & (np.abs(mv - cv) < cit['d'] / 2 + 40)
        return m

    return (lambda mu, mv: inside(zone, mu, mv)), monuments, st['u']


def cmd_real():
    os.makedirs(OUT, exist_ok=True)
    fr = frame(*CENTRE, HALF)
    zf, okf = os.path.join(WORK, 'grid_z.npy'), os.path.join(WORK, 'grid_ok.npy')
    if os.path.exists(zf) and os.path.exists(okf):
        z, ok = np.load(zf), np.load(okf)
    else:
        log('reading INEGI 1.5 m DTM and DSM')
        dtm, dsm, box = read_real(fr)
        log('rotating onto the 15.5 degree grid')
        z, ok = rotate(dtm, box, fr)
        s, ok2 = rotate(dsm, box, fr)
        del dtm, dsm
        ok &= ~ndimage.binary_dilation(ok2 & ok & ((s - z) > 2.5), iterations=1)   # buildings, trees: DTM there is interpolated
        del s, ok2
        np.save(zf, z)
        np.save(okf, ok)
    log(f'usable ground {ok.mean():.1%} of the box')
    segs = extract(z, ok, fr, **EDGE)
    np.savez_compressed(os.path.join(WORK, 'real_segments.npz'),
                        **{f'{s["axis"]}_{k}': v for s in segs for k, v in s.items() if isinstance(v, np.ndarray)})
    hs = ndimage.zoom(z, 0.25, order=1)
    np.savez_compressed(os.path.join(WORK, 'real_overview.npz'), z=hs, ok=ndimage.zoom(ok.astype(np.uint8), 0.25, order=0),
                        u_left=fr['u_left'], v_top=fr['v_top'], step=STEP * 4)
    del z, ok
    in_zone, monuments, street_u = zone_mask()
    sets = {
        'all': lambda mu, mv: np.ones(len(mu), bool),
        'zone': in_zone,
        'outside_zone': lambda mu, mv: ~in_zone(mu, mv),
        'no_monuments': lambda mu, mv: ~monuments(mu, mv),
        'west': lambda mu, mv: mu < street_u,
        'east': lambda mu, mv: mu >= street_u,
    }
    results, curves, tests = {}, {}, {}
    for name, keep in sets.items():
        log('scanning', name)
        res, cv, t = analyse(subset(segs, keep), CENTRE)
        results[name] = res
        curves.update({f'{name}_{k}': v for k, v in cv.items()})
        if name in ('all', 'west', 'east'):
            tests[name] = t
        g, l = res['global'], res['local']
        log(f"  segments {res['segments']}  pairs {l['npairs']}  local max {l['max']} at {l['q_at_max']} (bar95 {l['bar95']}, bar99 {l['bar99']})"
            f"  global max {g['max']} at {g['q_at_max']} (bar95 {g['bar95']}, bar99 {g['bar99']})  {res['seconds']}s")
        for t in l['targets'] + g['targets']:
            if t['p'] < 0.05:
                log('   target', t)
    # Decision rule, fixed before this run: a spacing is found only if it clears the 99% bar city-wide AND
    # clears p < 0.05 at the same spacing (+-0.4%) in both the west and the east half, scanned separately.
    verdict = {}
    for kind in ('local', 'global'):
        rows = []
        for p in results['all'][kind]['peaks']:
            if p['over_bar99']:
                w, e = p_at(tests['west'][kind], p['q']), p_at(tests['east'][kind], p['q'])
                rows.append({'q': p['q'], 'score': p['score'], 'west_p': w[1], 'east_p': e[1], 'found': w[1] < 0.05 and e[1] < 0.05})
        verdict[kind] = rows
        log(kind, 'replication', rows)
    results['verdict'] = verdict
    json.dump(results, open(os.path.join(OUT, 'real.json'), 'w'), indent=1)
    np.savez_compressed(os.path.join(WORK, 'real_curves.npz'), **curves)


def cmd_power():
    """How strong a ruler would the real-data test have caught? Snap a share of the real edges onto a 16.6 m
    ruler (one origin, 0.75 m scatter), leave the rest as they are, and rerun both tests."""
    sg = np.load(os.path.join(WORK, 'real_segments.npz'))
    base = [{k[2:]: sg[k] for k in sg.files if k.startswith(a + '_')} | {'axis': a} for a in ('u', 'v')]
    out = []
    for share in (0.02, 0.05, 0.10, 0.20):
        for seed in (1, 2):
            rng = np.random.default_rng(100 + seed)
            segs = []
            for s in base:
                s = dict(s)
                pick = rng.random(len(s['pos'])) < share
                p = s['pos'].copy()
                p[pick] = 16.6 * np.round(p[pick] / 16.6) + rng.normal(0, 0.75, pick.sum())
                s['pos'] = p
                s['mu' if s['axis'] == 'u' else 'mv'] = p
                segs.append(s)
            lt, gt = local_test(segs, 200, seed), global_test(segs, CENTRE, 200, seed)
            row = {'share': share, 'seed': seed, 'local': p_at(lt, 16.6), 'global': p_at(gt, 16.6),
                   'local_over_bar99': bool(p_at(lt, 16.6)[0] > np.percentile(lt['null'].max(axis=1), 99)),
                   'global_over_bar99': bool(p_at(gt, 16.6)[0] > np.percentile(gt['null'].max(axis=1), 99))}
            out.append(row)
            log(row)
    json.dump(out, open(os.path.join(OUT, 'power.json'), 'w'), indent=1)


def cmd_figures():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    INK, INK2, MUTED, SURF, RULE = '#0b0b0b', '#52514e', '#8a8984', '#fcfcfb', '#e6e5e0'
    SERIES = ['#2a78d6', '#eb6834', '#1baf7a']
    plt.rcParams.update({'font.family': 'Helvetica', 'font.size': 9, 'text.color': INK, 'axes.labelcolor': INK2,
                         'axes.edgecolor': RULE, 'xtick.color': INK2, 'ytick.color': INK2, 'axes.facecolor': SURF,
                         'figure.facecolor': SURF, 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.titlesize': 10, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left'})

    def bars(ax, b95, b99):
        for b, lab in ((b95, '95% chance bar'), (b99, '99% chance bar')):
            ax.axhline(b, color=MUTED, lw=1, ls=(0, (4, 3)))
            ax.annotate(lab, (0, b), xycoords=('axes fraction', 'data'), xytext=(4, 3), textcoords='offset points',
                        ha='left', fontsize=8, color=INK2)

    # 1. Synthetic: the global scan separates 80.5 cm, 83 cm and the vara at 20 units.
    c = np.load(os.path.join(WORK, 'synth_curves.npz'))
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    for col, (kind, lab) in zip(SERIES, (('alt805', '20 x 80.5 cm'), ('tmu20', '20 x 83 cm (TMU)'), ('vara20', '20 varas'))):
        q, y = c[f'{kind}_gq'], c[f'{kind}_greal']
        m = (q > 15.8) & (q < 17.1)
        ax.plot(q[m], y[m], color=col, lw=2, label=f'city built on {lab}')
        i = np.argmax(np.where(m, y, 0))
        ax.annotate(f'{q[i]:.2f} m', (q[i], y[i]), xytext=(0, 4), textcoords='offset points', ha='center', fontsize=8, color=INK)
    ax.set_xlabel('candidate spacing (m)')
    ax.set_ylabel('peak height (x local median)')
    ax.set_title('Synthetic cities: the city-wide scan finds each planted ruler, 0.5% apart')
    ax.legend(frameon=False, loc='upper left', bbox_to_anchor=(0.31, 0.98), fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'synthetic_rulers.png'), dpi=160)
    plt.close(fig)

    if not os.path.exists(os.path.join(WORK, 'real_curves.npz')):
        return
    # 2. Real: what was measured.
    ov = np.load(os.path.join(WORK, 'real_overview.npz'))
    sg = np.load(os.path.join(WORK, 'real_segments.npz'))
    z, step = ov['z'], float(ov['step'])
    gy, gx = np.gradient(z, step)
    az, alt = math.radians(315 - GRID_DEG), math.radians(40)
    hs = np.clip(np.sin(alt) * np.cos(np.arctan(np.hypot(gx, gy))) + np.cos(alt) * np.sin(np.arctan(np.hypot(gx, gy)))
                 * np.cos(az - np.arctan2(-gx, gy)), 0, 1)
    ext = [float(ov['u_left']), float(ov['u_left']) + z.shape[1] * step, float(ov['v_top']) - z.shape[0] * step, float(ov['v_top'])]
    fig, ax = plt.subplots(figsize=(7.2, 7.4))
    ax.imshow(np.where(ov['ok'] > 0, hs, np.nan), cmap='gray', extent=ext, vmin=-0.2, vmax=1.1, interpolation='bilinear')
    ns = [((p, a0), (p, a1)) for p, a0, a1 in zip(sg['u_pos'], sg['u_a0'], sg['u_a1'])]
    ew = [((a0, p), (a1, p)) for p, a0, a1 in zip(sg['v_pos'], sg['v_a0'], sg['v_a1'])]
    ax.add_collection(LineCollection(ns, colors=SERIES[0], linewidths=1.2, label=f'running with the grid N-S ({len(ns)})'))
    ax.add_collection(LineCollection(ew, colors=SERIES[1], linewidths=1.2, label=f'running with the grid E-W ({len(ew)})'))
    s = json.load(open(SITES))
    to_grid = lambda pts: np.array([(x * CA - y * SA, x * SA + y * CA) for x, y in pts])
    zg = to_grid(s['sites']['zone']['poly'])
    ax.plot(np.r_[zg[:, 0], zg[:1, 0]], np.r_[zg[:, 1], zg[:1, 1]], color=INK, lw=1, ls=(0, (3, 2)), label='protected archaeological zone')
    ax.set_xlim(ext[0], ext[1]); ax.set_ylim(ext[2], ext[3])
    ax.set_xlabel('metres east along the city grid'); ax.set_ylabel('metres north along the city grid (15.5° east of true north)')
    ax.set_title('Every grid-aligned edge the public 1.5 m terrain model shows\n'
                 'white: buildings, trees and missing data, left out', linespacing=1.5)
    ax.legend(frameon=True, facecolor=SURF, edgecolor=RULE, loc='lower left', fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'real_edges.png'), dpi=160)
    plt.close(fig)

    # 3. Real: both scans for the whole box, with the spacings named in advance.
    c = np.load(os.path.join(WORK, 'real_curves.npz'))
    fig, axs = plt.subplots(2, 1, figsize=(9.0, 6.0))
    marks = [(8.30, '10 TMU'), (10.79, '13 TMU'), (16.60, '20 TMU'), (20.0, '20 m'), (43.16, '52 TMU'), (215.8, '260 TMU')]
    for ax, key, title, ylab in ((axs[0], 'l', 'Local test: separations between facing parallel edges', 'score (local z)'),
                                 (axs[1], 'g', 'Global test: one ruler across the whole box', 'peak height (x local median)')):
        q, y = c[f'all_{key}q'], c[f'all_{key}real']
        ax.plot(q, y, color=SERIES[0], lw=1)
        bars(ax, float(c[f'all_{key}null95']), float(c[f'all_{key}null99']))
        ax.set_xscale('log')
        top = max(float(c[f'all_{key}null99']) * 1.25, float(y.max()) * 1.1)
        shown = [(qt, lab) for qt, lab in marks if q[0] <= qt <= q[-1]]
        for qt, _ in shown:
            ax.axvline(qt, color=RULE, lw=1, zorder=0)
        ax.set_ylim(top=top)
        ax.set_xticks([6, 8, 10, 15, 20, 30, 40, 60] + ([100, 200, 300] if key == 'g' else []))
        ax.get_xaxis().set_major_formatter(matplotlib.ticker.FormatStrFormatter('%g'))
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        top_ax = ax.secondary_xaxis('top')                               # names of the spacings fixed in advance
        top_ax.set_xticks([qt for qt, _ in shown], [lab for _, lab in shown], fontsize=7, color=INK2)
        top_ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        top_ax.tick_params(length=0)
        top_ax.spines['top'].set_visible(False)
        ax.set_title(title, pad=16); ax.set_ylabel(ylab)
    axs[1].set_xlabel('candidate spacing (m)')
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'real_scans.png'), dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    cmds = {'synth': cmd_synth, 'real': cmd_real, 'power': cmd_power, 'figures': cmd_figures}
    cmds.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
