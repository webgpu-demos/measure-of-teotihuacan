#!/usr/bin/env python3
"""Lidar point cloud -> 3D Gaussian splat (.ply), with no photos and no training.

A Gaussian splat is usually trained from photographs. Lidar already gives the geometry, so here each return
becomes one Gaussian directly: centred on the point, lying flat in the surface fitted to its neighbours, wide
enough to meet them and thin across the surface. Where the returns scatter in 3D (tree crowns) the fit has no
clear plane and the Gaussian comes out rounder. Colour is the sensor's RGB when the file has it; otherwise a
shaded relief baked in from each splat's normal, tinted by height, intensity or class.

The output is the standard 3D Gaussian splatting PLY (degree-0 colour), so SuperSplat, Spark, gsplat and most
splat tools open it. `python lidar/view.py out/site.ply` shows it in the viewer that comes with this folder,
which works with no internet connection. With drone photos of the same area, the same file is a good starting
point for real photometric training.

Runs locally with numpy, scipy and laspy (lazrs for LAZ). It reads files and writes files; nothing is sent
anywhere. A splat made from the team's lidar is the team's data, as is its .json sidecar, which holds the
offset back to the survey coordinates.

    python lidar/splat.py tiles/*.laz -o out/site.ply
    python lidar/splat.py survey.laz -o out/ground.ply --classes 2            # bare earth only
    python lidar/splat.py survey.laz -o out/crop.ply --bbox 516000 2177000 516600 2177600
    python lidar/view.py out/site.ply                                          # look at it
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

NOISE_CLASSES = (7, 18)                         # ASPRS low noise, high noise
SH_C0 = 0.28209479177387814                     # degree-0 spherical harmonic: colour = 0.5 + SH_C0 * f_dc
ENU_TO_YUP = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)    # (east, north, up) -> (x east, y up, z south)
RELIEF = np.array([[0.00, 0.30, 0.33, 0.24],    # height tint: low olive -> ochre -> pale stone
                   [0.35, 0.55, 0.50, 0.36],
                   [0.70, 0.76, 0.69, 0.53],
                   [1.00, 0.93, 0.90, 0.82]])
CLASS_RGB = {0: (.70, .66, .58), 1: (.70, .66, .58), 2: (.78, .70, .55), 3: (.55, .66, .38), 4: (.42, .58, .30),
             5: (.30, .48, .24), 6: (.80, .52, .42), 9: (.35, .50, .70), 17: (.60, .60, .62)}


# Reading ---------------------------------------------------------------------------------------------------

def las_files(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.rglob('*') if q.suffix.lower() in ('.las', '.laz'))
        else:
            out.append(p)
    if not out:
        sys.exit('No .las or .laz files found.')
    return out


def read_points(paths, bbox=None, classes=None, keep_noise=False, chunk=5_000_000):
    """Every return inside bbox (xmin, ymin, xmax, ymax) and the chosen classes, read in chunks so big tiles fit.

    Returns dict with xyz (float64, survey coordinates), cls (uint8) and, when the files carry them,
    rgb (float 0..1) and intensity.
    """
    import laspy
    parts = {'xyz': [], 'cls': [], 'rgb': [], 'intensity': []}
    has_rgb = True
    for path in las_files(paths):
        with laspy.open(path) as f:
            dims = set(f.header.point_format.dimension_names)
            has_rgb &= {'red', 'green', 'blue'} <= dims
            for pts in f.chunk_iterator(chunk):
                x, y, z = np.asarray(pts.x), np.asarray(pts.y), np.asarray(pts.z)
                c = np.asarray(pts.classification, np.uint8)
                m = np.ones(len(x), bool)
                if bbox is not None:
                    m &= (x >= bbox[0]) & (y >= bbox[1]) & (x <= bbox[2]) & (y <= bbox[3])
                if classes:
                    m &= np.isin(c, classes)
                elif not keep_noise:
                    m &= ~np.isin(c, NOISE_CLASSES)
                if not m.any():
                    continue
                parts['xyz'].append(np.column_stack([x[m], y[m], z[m]]))
                parts['cls'].append(c[m])
                parts['intensity'].append(np.asarray(pts.intensity, np.float32)[m])
                if has_rgb:
                    parts['rgb'].append(np.column_stack([np.asarray(pts.red)[m], np.asarray(pts.green)[m],
                                                         np.asarray(pts.blue)[m]]).astype(np.float32))
    if not parts['xyz']:
        sys.exit('No points left after the bbox and class filters.')
    out = {'xyz': np.concatenate(parts['xyz']), 'cls': np.concatenate(parts['cls']),
           'intensity': np.concatenate(parts['intensity'])}
    if has_rgb and parts['rgb']:
        rgb = np.concatenate(parts['rgb'])
        if rgb.max() > 0:                                   # many files declare RGB and leave it at zero
            out['rgb'] = rgb / (65535.0 if rgb.max() > 255 else 255.0)
    return out


# Thinning --------------------------------------------------------------------------------------------------

def voxel_thin(xyz, voxel, seed=0):
    """Indices keeping one random point per voxel-sized cube."""
    order = np.random.default_rng(seed).permutation(len(xyz))
    k = np.floor((xyz[order] - xyz.min(0)) / voxel).astype(np.int64)
    span = k.max(0) + 1
    key = (k[:, 0] * span[1] + k[:, 1]) * span[2] + k[:, 2]
    _, first = np.unique(key, return_index=True)
    return np.sort(order[first])


def thin_to(xyz, max_points, seed=0):
    """Voxel-thin until at most max_points remain, keeping density even rather than dropping at random."""
    if len(xyz) <= max_points:
        return np.arange(len(xyz)), 0.0
    area = np.ptp(xyz[:, 0]) * np.ptp(xyz[:, 1]) or 1.0
    voxel = math.sqrt(area / max_points)
    for _ in range(8):
        keep = voxel_thin(xyz, voxel, seed)
        if 0.85 * max_points <= len(keep) <= max_points:
            break
        voxel *= math.sqrt(len(keep) / max_points)          # surfaces: count scales with 1/voxel^2
    if len(keep) > max_points:
        keep = np.sort(np.random.default_rng(seed).choice(keep, max_points, replace=False))
    return keep, voxel


# Geometry --------------------------------------------------------------------------------------------------

def local_frames(xyz, k=12, chunk=400_000):
    """Per point: surface normal, the two in-surface axes and their spreads, and the spacing to its neighbours.

    Fits a plane to the k nearest neighbours (principal components). Returns
    axes (n, 3, 3) with columns [major tangent, minor tangent, normal] in a right-handed frame, normal pointing up;
    eig (n, 3) eigenvalues in the same order; spacing (n,) the point spacing 1/sqrt(density) of the surface
    around the point, from the disk its k neighbours fill. Unlike the nearest-neighbour distance, this means the
    same on a regular grid (a DEM) and on random returns, where the nearest neighbours are deceptively close;
    nearest (n,) the distance to the nearest neighbour.
    """
    from scipy.spatial import cKDTree
    n = len(xyz)
    k = min(k, n - 1)
    centre = xyz.mean(0)
    p = xyz - centre                                        # well-conditioned covariance in metres
    tree = cKDTree(p)
    axes = np.empty((n, 3, 3))
    eig = np.empty((n, 3))
    spacing = np.empty(n)
    nearest = np.empty(n)
    for s in range(0, n, chunk):
        q = p[s:s + chunk]
        d, idx = tree.query(q, k=k + 1, workers=-1)
        spacing[s:s + chunk] = d[:, k] * math.sqrt(math.pi / k)
        nearest[s:s + chunk] = d[:, 1]
        nb = p[idx]                                         # (m, k+1, 3), the point itself included
        nb = nb - nb.mean(1, keepdims=True)
        cov = np.einsum('mki,mkj->mij', nb, nb) / (k + 1)
        w, v = np.linalg.eigh(cov)                          # ascending: normal, minor, major
        normal, minor, major = v[:, :, 0], v[:, :, 1], v[:, :, 2]
        flip = normal[:, 2] < 0                             # airborne lidar sees surfaces from above
        normal[flip] *= -1
        frame = np.stack([major, minor, normal], axis=2)
        left = np.linalg.det(frame) < 0                     # keep it a rotation, not a reflection
        frame[left, :, 1] *= -1
        axes[s:s + chunk] = frame
        eig[s:s + chunk] = np.maximum(w[:, ::-1], 0)
    return axes, eig, spacing, nearest


def auto_coverage(spacing, nearest):
    """Splat size factor from how regular the sampling is.

    On a regular grid (a DEM) the nearest neighbour sits a full spacing away and 0.8 x spacing closes every gap.
    Random returns crowd some points together and leave gaps elsewhere (nearest ~ half the spacing), and need
    1.0 to close them. Real scan patterns fall in between. Returns (coverage, regularity).
    """
    regularity = float(np.median(nearest / np.maximum(spacing, 1e-12)))
    return float(np.clip(1.2 - 0.4 * regularity, 0.8, 1.0)), regularity


def quat_from_matrix(R):
    """Rotation matrices (n, 3, 3) -> unit quaternions (n, 4) as (w, x, y, z), w >= 0 (Shepperd's method)."""
    n = len(R)
    q = np.empty((n, 4))
    tr = R[:, 0, 0] + R[:, 1, 1] + R[:, 2, 2]
    case = np.stack([tr, R[:, 0, 0], R[:, 1, 1], R[:, 2, 2]], 1).argmax(1)
    m = case == 0
    s = np.sqrt(1 + tr[m]) * 2
    q[m] = np.column_stack([s / 4, (R[m, 2, 1] - R[m, 1, 2]) / s, (R[m, 0, 2] - R[m, 2, 0]) / s,
                            (R[m, 1, 0] - R[m, 0, 1]) / s])
    m = case == 1
    s = np.sqrt(1 + R[m, 0, 0] - R[m, 1, 1] - R[m, 2, 2]) * 2
    q[m] = np.column_stack([(R[m, 2, 1] - R[m, 1, 2]) / s, s / 4, (R[m, 0, 1] + R[m, 1, 0]) / s,
                            (R[m, 0, 2] + R[m, 2, 0]) / s])
    m = case == 2
    s = np.sqrt(1 + R[m, 1, 1] - R[m, 0, 0] - R[m, 2, 2]) * 2
    q[m] = np.column_stack([(R[m, 0, 2] - R[m, 2, 0]) / s, (R[m, 0, 1] + R[m, 1, 0]) / s, s / 4,
                            (R[m, 1, 2] + R[m, 2, 1]) / s])
    m = case == 3
    s = np.sqrt(1 + R[m, 2, 2] - R[m, 0, 0] - R[m, 1, 1]) * 2
    q[m] = np.column_stack([(R[m, 1, 0] - R[m, 0, 1]) / s, (R[m, 0, 2] + R[m, 2, 0]) / s,
                            (R[m, 1, 2] + R[m, 2, 1]) / s, s / 4])
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    q[q[:, 0] < 0] *= -1
    return q


def matrix_from_quat(q):
    """Quaternions (n, 4) as (w, x, y, z) -> rotation matrices (n, 3, 3), as 3DGS renderers build them."""
    w, x, y, z = (q / np.linalg.norm(q, axis=1, keepdims=True)).T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], 1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], 1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], 1)], 1)


def gaussian_scales(eig, spacing, coverage=0.8, max_aniso=2.0):
    """Standard deviations along [major, minor, normal].

    In the surface: coverage x the spacing to the neighbours, so neighbouring splats overlap without holes,
    stretched up to max_aniso where the neighbours line up (edges, wall tops). Across it: the measured spread
    of the neighbours off the plane (sensor noise on a wall, scatter in a crown), at least 5% of the in-surface
    size so surfaces stay crisp, and never more than the in-surface size.

    The spacing is held between 1/4 and 4 times its median: duplicate returns would give zero-size splats, and
    points at a corner of the data (neighbours on one side only) would give splats that spill past it.
    """
    med = np.median(spacing)
    sigma = coverage * np.clip(spacing, 0.25 * med, 4 * med)
    ratio = np.sqrt(np.clip(eig[:, 0] / np.maximum(eig[:, 1], 1e-12), 1, max_aniso ** 2))
    major = sigma * np.sqrt(ratio)
    minor = sigma / np.sqrt(ratio)
    across = np.clip(np.sqrt(eig[:, 2]), 0.05 * sigma, minor)
    return np.column_stack([major, minor, across])


# Colour ----------------------------------------------------------------------------------------------------

def sun_vector(azimuth=315.0, altitude=45.0):
    """Direction towards the sun in (east, north, up); azimuth clockwise from north, as in a hillshade."""
    a, h = math.radians(azimuth), math.radians(altitude)
    return np.array([math.sin(a) * math.cos(h), math.cos(a) * math.cos(h), math.sin(h)])


def ramp(t, stops=RELIEF):
    t = np.clip(t, 0, 1)
    return np.column_stack([np.interp(t, stops[:, 0], stops[:, c]) for c in (1, 2, 3)])


def stretch(v, lo=2, hi=98):
    a, b = np.percentile(v, [lo, hi])
    return (v - a) / (b - a) if b > a else np.full(len(v), 0.5)


def colours(pts, normal, mode='auto', sun=(315.0, 45.0), ambient=0.35):
    """RGB in 0..1 per point. 'rgb' is the sensor's colour as is; other modes are a tint times a baked hillshade."""
    if mode == 'auto':
        mode = 'rgb' if 'rgb' in pts else 'height'
    if mode == 'rgb':
        if 'rgb' not in pts:
            sys.exit('These files carry no RGB. Use --color height, intensity or class.')
        return pts['rgb'], mode
    if mode == 'height':
        tint = ramp(stretch(pts['xyz'][:, 2], 1, 99))
    elif mode == 'intensity':
        g = 0.25 + 0.65 * np.clip(stretch(pts['intensity']), 0, 1)
        tint = np.column_stack([g, g * 0.97, g * 0.92])
    elif mode == 'class':
        lut = np.tile(np.array(CLASS_RGB[1]), (256, 1))
        for c, rgb in CLASS_RGB.items():
            lut[c] = rgb
        tint = lut[pts['cls']]
    else:
        sys.exit(f'Unknown colour mode {mode!r}.')
    light = np.clip(normal @ sun_vector(*sun), 0, 1)
    return np.clip(tint * (ambient + (1 - ambient) * light)[:, None], 0, 1), mode


# Assembly and output ---------------------------------------------------------------------------------------

def make_splats(pts, k=12, coverage=None, max_aniso=2.0, color='auto', sun=(315.0, 45.0), opacity=0.98,
                yup=True, isolated=8.0):
    """Points (from read_points) -> dict of splat arrays, in a local frame centred on the data.

    coverage=None picks the splat size factor from the sampling pattern (auto_coverage).
    """
    xyz = pts['xyz']
    axes, eig, spacing, nearest = local_frames(xyz, k)
    keep = spacing <= isolated * np.median(spacing)         # lone returns: birds, multipath, wires
    if not keep.all():
        pts = {key: v[keep] for key, v in pts.items()}
        xyz, axes, eig, spacing, nearest = pts['xyz'], axes[keep], eig[keep], spacing[keep], nearest[keep]
    auto, regularity = auto_coverage(spacing, nearest)
    coverage = auto if coverage is None else coverage
    scales = gaussian_scales(eig, spacing, coverage, max_aniso)
    rgb, mode = colours(pts, axes[:, :, 2], color, sun)
    lo, hi = xyz.min(0), xyz.max(0)
    offset = np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, float(np.median(xyz[:, 2]))])
    local = xyz - offset
    if yup:
        local = local @ ENU_TO_YUP.T
        axes = ENU_TO_YUP @ axes
    return {'xyz': local.astype(np.float32), 'normal': axes[:, :, 2].astype(np.float32),
            'rgb': rgb.astype(np.float32), 'opacity': np.full(len(local), opacity, np.float32),
            'scale': scales.astype(np.float32), 'rot': quat_from_matrix(axes).astype(np.float32),
            'offset': offset, 'spacing': float(np.median(spacing)), 'dropped': int((~keep).sum()), 'color': mode,
            'coverage': coverage, 'regularity': regularity}


PLY_FIELDS = (['x', 'y', 'z', 'nx', 'ny', 'nz', 'f_dc_0', 'f_dc_1', 'f_dc_2', 'opacity']
              + ['scale_0', 'scale_1', 'scale_2', 'rot_0', 'rot_1', 'rot_2', 'rot_3'])


def write_ply(path, s):
    """The 3D Gaussian splatting PLY: log scales, logit opacity, colour as the degree-0 SH coefficient."""
    n = len(s['xyz'])
    rec = np.empty(n, dtype=[(f, '<f4') for f in PLY_FIELDS])
    for i, a in enumerate('xyz'):
        rec[a] = s['xyz'][:, i]
        rec['n' + a] = s['normal'][:, i]
    for i in range(3):
        rec[f'f_dc_{i}'] = (s['rgb'][:, i] - 0.5) / SH_C0
        rec[f'scale_{i}'] = np.log(s['scale'][:, i])
    for i in range(4):
        rec[f'rot_{i}'] = s['rot'][:, i]
    o = np.clip(s['opacity'], 1e-4, 1 - 1e-4)
    rec['opacity'] = np.log(o / (1 - o))
    header = ['ply', 'format binary_little_endian 1.0', 'comment made by lidar/splat.py from lidar returns',
              f'element vertex {n}'] + [f'property float {f}' for f in PLY_FIELDS] + ['end_header']
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as f:
        f.write(('\n'.join(header) + '\n').encode('ascii'))
        f.write(rec.tobytes())


def read_ply(path):
    """Read back a binary float PLY written by write_ply (or any 3DGS PLY with float properties)."""
    data = Path(path).read_bytes()
    end = data.index(b'end_header\n') + len(b'end_header\n')
    lines = data[:end].decode('ascii').splitlines()
    n = int(next(ln.split()[2] for ln in lines if ln.startswith('element vertex')))
    names = [ln.split()[2] for ln in lines if ln.startswith('property float')]
    return np.frombuffer(data, dtype=[(f, '<f4') for f in names], count=n, offset=end)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split('\n\n', 1)[1])
    ap.add_argument('inputs', nargs='+', help='LAS/LAZ files or folders of them (tiles are merged)')
    ap.add_argument('-o', '--out', required=True, help='output .ply; a .json sidecar is written next to it')
    ap.add_argument('--bbox', nargs=4, type=float, metavar=('XMIN', 'YMIN', 'XMAX', 'YMAX'),
                    help='crop, in the files\' own coordinates')
    ap.add_argument('--classes', nargs='+', type=int, help='keep only these ASPRS classes (2 = ground)')
    ap.add_argument('--keep-noise', action='store_true', help='keep classes 7 and 18 (dropped by default)')
    ap.add_argument('--max-points', type=int, default=3_000_000,
                    help='voxel-thin to at most this many splats (browsers are comfortable up to a few million)')
    ap.add_argument('--voxel', type=float, help='thin to one point per cube of this size (m), instead')
    ap.add_argument('--color', default='auto', choices=['auto', 'rgb', 'height', 'intensity', 'class'],
                    help='auto: the sensor RGB if present, else height-tinted relief')
    ap.add_argument('--sun', nargs=2, type=float, default=(315.0, 45.0), metavar=('AZIMUTH', 'ALTITUDE'))
    ap.add_argument('--neighbors', type=int, default=12, help='neighbours for the surface fit')
    ap.add_argument('--coverage', type=float,
                    help='splat size as a fraction of point spacing (default: 0.8 to 1.0, chosen from how regular '
                         'the sampling is); raise it if the surface shows holes, lower it for a crisper look')
    ap.add_argument('--zup', action='store_true', help='keep z up (default: y up, x east, z south, as in three.js)')
    ap.add_argument('--seed', type=int, default=0)
    a = ap.parse_args(argv)

    t0 = time.time()
    pts = read_points(a.inputs, a.bbox, a.classes, a.keep_noise)
    n_in = len(pts['xyz'])
    if a.voxel:
        keep, voxel = voxel_thin(pts['xyz'], a.voxel, a.seed), a.voxel
    else:
        keep, voxel = thin_to(pts['xyz'], a.max_points, a.seed)
    pts = {k: v[keep] for k, v in pts.items()}
    print(f'{n_in:,} returns read, {len(keep):,} kept' + (f' (voxel {voxel:.2f} m)' if voxel else ''), flush=True)
    s = make_splats(pts, a.neighbors, a.coverage, color=a.color, sun=tuple(a.sun), yup=not a.zup)
    write_ply(a.out, s)
    lo, hi = s['xyz'].min(0), s['xyz'].max(0)
    meta = {'splats': len(s['xyz']), 'returns_read': n_in, 'voxel_m': voxel, 'isolated_dropped': s['dropped'],
            'median_spacing_m': round(s['spacing'], 3), 'color': s['color'],
            'coverage': round(s['coverage'], 3), 'regularity': round(s['regularity'], 3),
            'axes': 'z up, survey axes' if a.zup else 'x east, y up, z south',
            'offset': [round(v, 3) for v in s['offset']],
            'note': 'survey coordinates = splat coordinates mapped back to (east, north, up) + offset',
            'bounds': [lo.round(2).tolist(), hi.round(2).tolist()]}
    Path(a.out).with_suffix('.json').write_text(json.dumps(meta, indent=1))
    mb = Path(a.out).stat().st_size / 1e6
    print(f'{len(s["xyz"]):,} splats -> {a.out} ({mb:.0f} MB, {s["color"]} colour, median spacing '
          f'{s["spacing"]:.2f} m, splat size {s["coverage"]:.2f} x spacing) in {time.time() - t0:.1f} s')


if __name__ == '__main__':
    main()
