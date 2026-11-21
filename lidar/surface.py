"""surface: lidar point clouds to bare-earth surfaces and solid meshes, the same way for every case study.

Runs on the lab machine, next to the data. Nothing leaves it.

    python lidar/surface.py run cases.toml            # every case study in the file
    python lidar/surface.py run cases.toml --only moon-plaza
    python lidar/surface.py run cases.toml --dry-run  # show the settings each case will use, run nothing
    python lidar/surface.py mesh3d scan.ply -o out/sculpture   # close-range scans: a true 3D mesh

For each case study `run` writes, into <out>/<case>/:
    classified.laz   every point with its class (2 = ground), to check the filter in CloudCompare or QGIS
    ground.laz       ground points only
    dtm.tif          bare earth: the mean of the ground points in each cell where there are enough of them,
                     interpolated across the triangles between them elsewhere
    points.tif       ground points per cell (0 = no measurement there: the surface is an estimate)
    gap.tif          metres from each cell to the nearest measured cell (0 = measured)
    dsm.tif          highest point per cell, trees and houses included (context only)
    removed.tif      height of everything the ground filter took away (dsm - dtm): trees and houses, and any
                     ancient feature it took by mistake. Look here before trusting a case study's surface.
    hillshade.tif, slope.tif, slrm.tif   relief views (multi-directional light, slope, local relief)
    surface.ply / surface.obj            the DTM as a solid mesh, coloured: stone = measured,
                     orange = estimated, stronger the farther it is from a measurement
    run.json         every setting, the exact PDAL pipelines, tool versions, file hashes and the counts
and <out>/cases.csv compares the case studies side by side.

Every setting can be given once under [defaults] and changed per case. Ground filters are a trade-off
(a 1.5 m platform and a low house have the same shape to a filter), so the settings a case used are
recorded with it, and different cases are only comparable if their settings match.
"""
import argparse
import csv
import glob
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from copy import deepcopy
from pathlib import Path

import numpy as np

DEFAULTS = {
    'resolution': 'auto',     # metres, or 'auto' = about two ground points per cell
    'ground': 'delivered',    # 'delivered': trust the survey's own class 2; 'smrf' or 'csf': classify here
    'srs': '',                # only used when the files carry no coordinate system, e.g. 'EPSG:32614'
    'bounds': [],             # [xmin, ymin, xmax, ymax] to cut one case study out of a larger survey
    'noise': True,            # flag isolated points and low multipath points as noise (class 7) first
    'smrf': {'cell': 1.0, 'slope': 0.6, 'window': 14.0, 'threshold': 0.5, 'scalar': 1.25},
    'csf': {'resolution': 0.5, 'rigidness': 1, 'threshold': 0.5, 'step': 0.65, 'smooth': True},
    'min_points': 2,          # cells with at least this many ground points take their mean (less noise)
    'mesh': True,
    'mesh_formats': ['ply', 'obj'],
    'relief': True,
    'slrm_radius': 10.0,      # metres, for the local relief model
    'hash_inputs': True,      # SHA-256 of every input file in run.json (slow on very large surveys)
}

STONE = np.array([205, 198, 186], float)
ESTIMATE = np.array([235, 120, 40], float)


# Tools ----------------------------------------------------------------------------------------------
def tool(name):
    """PDAL and GDAL command-line tools: on PATH (an activated environment) or next to this Python."""
    here = Path(sys.executable).parent
    for p in (shutil.which(name), shutil.which(name, path=str(here)),
              shutil.which(name, path=str(here / 'Library' / 'bin'))):
        if p:
            return p
    raise SystemExit(f'{name} not found. Create the environment in lidar/environment.yml and activate it.')


def sh(*args, capture=False):
    r = subprocess.run([str(a) for a in args], check=True, capture_output=True, text=True)
    return r.stdout if capture else None


def pdal_pipeline(stages, path):
    """Write the pipeline next to the outputs (it is part of the record) and run it."""
    path.write_text(json.dumps({'pipeline': stages}, indent=1))
    try:
        sh(tool('pdal'), 'pipeline', path)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f'PDAL failed on {path.name}:\n{e.stderr}')
    return stages


def versions():
    out = {'python': sys.version.split()[0], 'numpy': np.__version__}
    try:
        out['pdal'] = sh(tool('pdal'), '--version', capture=True).split('(')[0].replace('pdal', '').strip(' -\n')
        out['gdal'] = sh(tool('gdalinfo'), '--version', capture=True).strip()
    except Exception as e:                                   # noqa: BLE001 - versions are informative only
        out['error'] = str(e)
    return out


def sha256(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


# Rasters without the GDAL Python bindings: PDAL writes ENVI, numpy reads it, gdal_translate makes GeoTIFFs
ENVI_TYPES = {1: np.uint8, 2: np.int16, 3: np.int32, 4: np.float32, 5: np.float64, 12: np.uint16, 13: np.uint32}


def envi_header(path):
    for h in (Path(str(path) + '.hdr'), Path(path).with_suffix('.hdr')):
        if h.exists():
            return h
    raise FileNotFoundError(f'no ENVI header for {path}')


def read_envi(path):
    """-> {band name: array (lines, samples)}, by name because PDAL orders bands its own way. Empty cells are NaN."""
    text = envi_header(path).read_text()
    get = lambda k: re.search(rf'^{k}\s*=\s*(\S+)', text, re.M | re.I).group(1)
    w, h, b = int(get('samples')), int(get('lines')), int(get('bands'))
    dt = np.dtype(ENVI_TYPES[int(get('data type'))]).newbyteorder('>' if get('byte order') == '1' else '<')
    a = np.fromfile(path, dtype=dt).astype(np.float64)
    order = get('interleave').lower()
    a = (a.reshape(b, h, w) if order == 'bsq' else a.reshape(h, b, w).transpose(1, 0, 2) if order == 'bil'
         else a.reshape(h, w, b).transpose(2, 0, 1))
    ignore = re.search(r'^data ignore value\s*=\s*(\S+)', text, re.M | re.I)
    if ignore:
        a[a == float(ignore.group(1))] = np.nan
    names = re.search(r'^band names\s*=\s*\{([^}]*)\}', text, re.M | re.I | re.S)
    names = [n.strip() for n in names.group(1).split(',')] if names else [f'band{i + 1}' for i in range(b)]
    return dict(zip(names, a))


def band(path):
    """The only band of a one-band raster."""
    (a,) = read_envi(path).values()
    return a


def read_raster(path):
    """Any GDAL raster (a GeoTIFF from this tool, say) -> 2D array, through a temporary ENVI copy."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bil = Path(d) / 'r.bil'
        sh(tool('gdal_translate'), '-q', '-of', 'ENVI', '-ot', 'Float64', path, bil)
        return next(iter(read_envi(bil).values()))


def write_geotiff(arr, ref, out, srs=''):
    """Write one float32 band on the same grid as the ENVI raster `ref`, then convert to a compressed GeoTIFF."""
    text = envi_header(ref).read_text()
    text = re.sub(r'^data type\s*=.*$', 'data type = 4', text, flags=re.M | re.I)
    text = re.sub(r'^bands\s*=.*$', 'bands = 1', text, flags=re.M | re.I)
    text = re.sub(r'^byte order\s*=.*$', f'byte order = {0 if sys.byteorder == "little" else 1}', text, flags=re.M | re.I)
    text = re.sub(r'^interleave\s*=.*$', 'interleave = bsq', text, flags=re.M | re.I)
    text = re.sub(r'^band names\s*=\s*\{[^}]*\}\s*', '', text, flags=re.M | re.I | re.S)
    text = re.sub(r'^data ignore value\s*=.*$\n?', '', text, flags=re.M | re.I)
    text = re.sub(r'^data offset values\s*=.*$\n?', '', text, flags=re.M | re.I)
    raw = Path(out).with_suffix('.raw.bil')
    np.asarray(arr, np.float32).tofile(raw)
    Path(str(raw) + '.hdr').write_text(text)
    args = [tool('gdal_translate'), '-q', '-of', 'GTiff', '-a_nodata', 'nan',
            '-co', 'COMPRESS=DEFLATE', '-co', 'PREDICTOR=3', '-co', 'TILED=YES']
    if srs:
        args += ['-a_srs', srs]
    sh(*args, raw, out)
    raw.unlink()
    Path(str(raw) + '.hdr').unlink()
    for extra in Path(out).parent.glob(Path(out).name + '.aux.xml'):
        extra.unlink()


# Settings -------------------------------------------------------------------------------------------
def merge(base, over):
    out = deepcopy(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_cases(path):
    path = Path(path)
    cfg = tomllib.loads(path.read_text())
    unknown = set(cfg.get('defaults', {})) - set(DEFAULTS)
    if unknown:
        raise SystemExit(f'unknown setting(s) under [defaults]: {sorted(unknown)}')
    base = merge(DEFAULTS, cfg.get('defaults', {}))
    cases = []
    for c in cfg.get('case', []):
        if 'name' not in c or 'input' not in c:
            raise SystemExit('every [[case]] needs a name and an input')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', c['name']):
            raise SystemExit(f'case name {c["name"]!r}: use letters, digits, - _ . only (it becomes a folder)')
        bad = set(c) - set(DEFAULTS) - {'name', 'input', 'note'}
        if bad:
            raise SystemExit(f'case {c["name"]}: unknown setting(s) {sorted(bad)}')
        s = merge(base, {k: v for k, v in c.items() if k not in ('input',)})
        pats = [c['input']] if isinstance(c['input'], str) else c['input']
        files = sorted({f for p in pats for f in glob.glob(str(path.parent / p) if not os.path.isabs(p) else p)})
        if not files:
            raise SystemExit(f'case {c["name"]}: no files match {pats}')
        s['files'] = files
        if s['ground'] not in ('delivered', 'smrf', 'csf'):
            raise SystemExit(f'case {c["name"]}: ground must be delivered, smrf or csf')
        cases.append(s)
    if len({c['name'] for c in cases}) != len(cases):
        raise SystemExit('case names must be unique')
    return cases


# The grid -------------------------------------------------------------------------------------------
def data_bounds(files):
    b = [math.inf, math.inf, -math.inf, -math.inf]
    for f in files:
        s = json.loads(sh(tool('pdal'), 'info', '--summary', f, capture=True))['summary']['bounds']
        b = [min(b[0], s['minx']), min(b[1], s['miny']), max(b[2], s['maxx']), max(b[3], s['maxy'])]
    return b


def grid(bounds, res):
    """Cells aligned to multiples of res: (x0, y0, width, height), origin at the lower left."""
    x0, y0 = math.floor(bounds[0] / res + 1e-9) * res, math.floor(bounds[1] / res + 1e-9) * res
    w = max(1, math.ceil((bounds[2] - x0) / res - 1e-9))
    h = max(1, math.ceil((bounds[3] - y0) / res - 1e-9))
    return x0, y0, w, h


def grid_opts(g, res):
    x0, y0, w, h = g
    return {'resolution': res, 'origin_x': x0, 'origin_y': y0, 'width': w, 'height': h}


# One case study -------------------------------------------------------------------------------------
def readers(s):
    st = []
    for f in s['files']:
        r = {'type': 'readers.las', 'filename': f}
        if s['srs']:
            r['default_srs'] = s['srs']
        st.append(r)
    if len(st) > 1:
        st.append({'type': 'filters.merge'})
    if s['bounds']:
        xmin, ymin, xmax, ymax = s['bounds']
        st.append({'type': 'filters.crop', 'bounds': f'([{xmin},{xmax}],[{ymin},{ymax}])'})
    return st


def classify_stages(s):
    st = []
    if s['ground'] != 'delivered':
        st.append({'type': 'filters.assign', 'value': 'Classification = 1'})        # forget old classes
    if s['noise']:
        st += [{'type': 'filters.outlier', 'method': 'statistical', 'mean_k': 8, 'multiplier': 3.0},
               {'type': 'filters.elm', 'cell': 10.0, 'threshold': 1.0}]
    ignore = 'Classification[7:7]'
    if s['ground'] == 'smrf':
        st.append({'type': 'filters.smrf', 'ignore': ignore, **s['smrf']})
    elif s['ground'] == 'csf':
        st.append({'type': 'filters.csf', 'ignore': ignore, **s['csf']})
    return st


def run_case(s, out_root):
    t0 = time.time()
    out = Path(out_root) / s['name']
    work = out / 'work'
    work.mkdir(parents=True, exist_ok=True)
    rec = {'case': s['name'], 'settings': {k: v for k, v in s.items() if k != 'files'},
           'inputs': [{'file': f, 'bytes': os.path.getsize(f),
                       **({'sha256': sha256(f)} if s['hash_inputs'] else {})} for f in s['files']],
           'versions': versions(), 'pipelines': {}}
    print(f'[{s["name"]}] {len(s["files"])} file(s), ground = {s["ground"]}')

    # 1. Classify (or keep the survey's classes), write the classified cloud and the ground points
    cls = out / 'classified.laz'
    rec['pipelines']['classify'] = pdal_pipeline(
        readers(s) + classify_stages(s) +
        [{'type': 'writers.las', 'filename': str(cls), 'compression': True, 'forward': 'all', 'extra_dims': 'all'},
         {'type': 'filters.range', 'limits': 'Classification[2:2]'},
         {'type': 'writers.las', 'filename': str(out / 'ground.laz'), 'compression': True, 'forward': 'all'}],
        work / 'classify.json')
    summ = json.loads(sh(tool('pdal'), 'info', '--summary', out / 'ground.laz', capture=True))['summary']
    if not summ.get('num_points') and not summ.get('metadata', {}).get('count'):
        raise SystemExit(f'[{s["name"]}] no ground points. With ground = "delivered" the survey must carry class 2; '
                         'otherwise use ground = "smrf" or "csf".')

    # 2. Ground density -> resolution
    bounds = s['bounds'] or data_bounds([cls])
    g1 = grid(bounds, 1.0)
    dens_path = work / 'density.bil'
    pdal_pipeline([{'type': 'readers.las', 'filename': str(out / 'ground.laz')},
                   {'type': 'writers.gdal', 'filename': str(dens_path), 'gdaldriver': 'ENVI', 'binmode': True,
                    'output_type': 'count', **grid_opts(g1, 1.0)}], work / 'density.json')
    per_m2 = band(dens_path)
    occupied = per_m2[np.isfinite(per_m2) & (per_m2 > 0)]
    density = float(np.median(occupied)) if occupied.size else 0.0
    res = s['resolution']
    if res == 'auto':
        res = max(0.1, math.ceil(math.sqrt(2.0 / max(density, 1e-6)) / 0.05 - 1e-9) * 0.05)
    res = round(float(res), 4)
    g = grid(bounds, res)
    rec['ground_points_per_m2'] = round(density, 2)
    rec['resolution_m'] = res
    rec['grid'] = {'origin_lower_left': [g[0], g[1]], 'width': g[2], 'height': g[3]}
    print(f'[{s["name"]}] ground density {density:.1f} pts/m2 -> cells of {res} m ({g[2]} x {g[3]})')

    # 3. Rasters: ground count + mean per cell, the triangulated surface, the highest point per cell
    stats, tin, top = work / 'ground_stats.bil', work / 'tin.bil', work / 'top.bil'
    rec['pipelines']['dtm'] = pdal_pipeline(
        [{'type': 'readers.las', 'filename': str(out / 'ground.laz')},
         {'type': 'writers.gdal', 'filename': str(stats), 'gdaldriver': 'ENVI', 'binmode': True,
          'output_type': ['count', 'mean'], **grid_opts(g, res)},
         {'type': 'filters.delaunay'},
         {'type': 'filters.faceraster', **grid_opts(g, res)},
         {'type': 'writers.raster', 'filename': str(tin), 'gdaldriver': 'ENVI'}], work / 'dtm.json')
    rec['pipelines']['dsm'] = pdal_pipeline(
        [{'type': 'readers.las', 'filename': str(cls)},
         {'type': 'filters.range', 'limits': 'Classification![7:7]'},
         {'type': 'writers.gdal', 'filename': str(top), 'gdaldriver': 'ENVI', 'binmode': True,
          'output_type': 'max', **grid_opts(g, res)}], work / 'dsm.json')
    st = read_envi(stats)
    count, mean = np.nan_to_num(st['count']), st['mean']
    tin_z = band(tin)
    dsm = band(top)
    dtm = np.where(count >= s['min_points'], mean, tin_z)
    dtm = np.where(np.isfinite(dtm), dtm, mean)          # edge cells the triangles miss: their own points

    from scipy import ndimage
    measured = count > 0
    gap = ndimage.distance_transform_edt(~measured) * res if measured.any() else np.full(count.shape, np.inf)
    gap = np.where(np.isfinite(dtm), gap, np.nan)

    srs = s['srs']
    write_geotiff(dtm, stats, out / 'dtm.tif', srs)
    write_geotiff(count, stats, out / 'points.tif', srs)
    write_geotiff(gap, stats, out / 'gap.tif', srs)
    write_geotiff(dsm, stats, out / 'dsm.tif', srs)
    write_geotiff(np.where(dsm - dtm > 0.1, dsm - dtm, np.where(np.isfinite(dsm - dtm), 0.0, np.nan)),
                  stats, out / 'removed.tif', srs)

    # 4. Relief views
    if s['relief']:
        sh(tool('gdaldem'), 'hillshade', '-q', '-multidirectional', '-compute_edges', out / 'dtm.tif', out / 'hillshade.tif')
        sh(tool('gdaldem'), 'slope', '-q', '-compute_edges', out / 'dtm.tif', out / 'slope.tif')
        write_geotiff(slrm(dtm, s['slrm_radius'] / res), stats, out / 'slrm.tif', srs)

    # 5. The solid mesh
    if s['mesh']:
        x0, y0, w, h = g
        rec['mesh'] = write_grid_mesh(dtm, gap, x0, y0 + h * res, res, out / 'surface', s['mesh_formats'], srs)

    valid = np.isfinite(dtm)
    rec['counts'] = {
        'cells': int(valid.sum()),
        'measured_cells_pct': round(100 * float((measured & valid).sum()) / max(1, valid.sum()), 2),
        'well_measured_cells_pct': round(100 * float(((count >= s['min_points']) & valid).sum()) / max(1, valid.sum()), 2),
        'estimated_more_than_2m_from_data_pct': round(100 * float((gap > 2.0).sum()) / max(1, valid.sum()), 2),
    }
    outputs = sorted(p for p in out.iterdir() if p.is_file() and p.name != 'run.json')
    rec['outputs'] = {p.name: {'bytes': p.stat().st_size, 'sha256': sha256(p)} for p in outputs}
    rec['seconds'] = round(time.time() - t0, 1)
    (out / 'run.json').write_text(json.dumps(rec, indent=1))
    print(f'[{s["name"]}] {rec["counts"]["measured_cells_pct"]}% of cells measured; done in {rec["seconds"]} s')
    return rec


def slrm(dtm, radius_cells):
    """Local relief: the DTM minus its mean over a window, so small rises and dips stand out on any slope."""
    from scipy import ndimage
    ok = np.isfinite(dtm)
    size = max(3, int(2 * round(radius_cells) + 1))
    num = ndimage.uniform_filter(np.where(ok, dtm, 0.0), size)
    den = ndimage.uniform_filter(ok.astype(float), size)
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(ok & (den > 0), dtm - num / den, np.nan)


# Meshes ---------------------------------------------------------------------------------------------
def confidence_colours(gap, full_at=5.0):
    """Stone where measured; towards orange the farther the surface is from a measurement (faint for one
    missing cell between measured ones, full orange at `full_at` metres and beyond)."""
    t = np.clip(np.nan_to_num(gap, nan=full_at) / full_at, 0, 1)
    t = np.where(gap > 0, 0.15 + 0.85 * t, 0.0)
    return (STONE * (1 - t[..., None]) + ESTIMATE * t[..., None]).round().astype(np.uint8)


def offset_for(xyz):
    """A round local origin, so float32 mesh coordinates keep millimetres (UTM eastings do not)."""
    return np.floor(np.nanmin(xyz, axis=0) / 100.0) * 100.0


def write_mesh(path_stem, xyz, faces, rgb, gap, formats, srs='', note=''):
    off = offset_for(xyz)
    local = (xyz - off).astype(np.float32)
    stem = Path(path_stem)
    written = {}
    if 'ply' in formats:
        v = np.zeros(len(local), dtype=[('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('red', 'u1'), ('green', 'u1'),
                                        ('blue', 'u1'), ('gap_m', '<f4')])
        v['x'], v['y'], v['z'] = local.T
        v['red'], v['green'], v['blue'] = rgb.T
        v['gap_m'] = gap
        f = np.zeros(len(faces), dtype=[('n', 'u1'), ('i', '<i4', (3,))])
        f['n'], f['i'] = 3, faces
        head = ['ply', 'format binary_little_endian 1.0',
                f'comment offset {off[0]:.3f} {off[1]:.3f} {off[2]:.3f} (add to x y z for real coordinates)']
        if srs:
            head.append(f'comment crs {srs}')
        if note:
            head.append(f'comment {note}')
        head += [f'element vertex {len(v)}', 'property float x', 'property float y', 'property float z',
                 'property uchar red', 'property uchar green', 'property uchar blue', 'property float gap_m',
                 f'element face {len(f)}', 'property list uchar int vertex_indices', 'end_header']
        with open(stem.with_suffix('.ply'), 'wb') as fh:
            fh.write(('\n'.join(head) + '\n').encode('ascii'))
            fh.write(v.tobytes())
            fh.write(f.tobytes())
        written['ply'] = stem.with_suffix('.ply').name
    if 'obj' in formats:
        with open(stem.with_suffix('.obj'), 'w') as fh:
            fh.write(f'# offset {off[0]:.3f} {off[1]:.3f} {off[2]:.3f} (add to x y z for real coordinates)\n')
            if srs:
                fh.write(f'# crs {srs}\n')
            c = rgb / 255.0
            np.savetxt(fh, np.column_stack([local, c]), fmt='v %.4f %.4f %.4f %.3f %.3f %.3f')
            np.savetxt(fh, faces + 1, fmt='f %d %d %d')
        written['obj'] = stem.with_suffix('.obj').name
    return {'files': written, 'offset': off.tolist(), 'vertices': int(len(xyz)), 'triangles': int(len(faces))}


def grid_mesh(z, x0, y_top, res):
    """Cell centres -> vertices; two triangles per square of four valid cells, facing up."""
    h, w = z.shape
    ok = np.isfinite(z)
    idx = np.full((h, w), -1, np.int64)
    idx[ok] = np.arange(ok.sum())
    ii, jj = np.nonzero(ok)
    xyz = np.column_stack([x0 + (jj + 0.5) * res, y_top - (ii + 0.5) * res, z[ok]])
    a, b, c, d = idx[:-1, :-1], idx[:-1, 1:], idx[1:, :-1], idx[1:, 1:]
    t1 = np.stack([a, c, b], -1).reshape(-1, 3)
    t2 = np.stack([b, c, d], -1).reshape(-1, 3)
    faces = np.concatenate([t1[(t1 >= 0).all(1)], t2[(t2 >= 0).all(1)]])
    return xyz, faces, ok


def write_grid_mesh(dtm, gap, x0, y_top, res, stem, formats, srs=''):
    xyz, faces, ok = grid_mesh(dtm, x0, y_top, res)
    return write_mesh(stem, xyz, faces, confidence_colours(gap[ok]), gap[ok], formats, srs,
                      note=f'surface.py DTM mesh, {res} m cells; gap_m = metres to the nearest measured cell')


# Close-range scans: a true 3D surface -------------------------------------------------------------------
def read_points(path):
    """-> (points, normals or None). Normals exported by the scanning or photogrammetry software are kept:
    they know which side each surface faces, which estimating them afterwards can only guess."""
    p = str(path)
    if p.lower().endswith(('.las', '.laz')):
        import laspy
        las = laspy.read(p)
        return np.column_stack([las.x, las.y, las.z]), None
    if p.lower().endswith(('.xyz', '.txt', '.csv', '.pts')):
        a = np.loadtxt(p, delimiter=',' if p.lower().endswith('.csv') else None, ndmin=2)
        return a[:, :3], (a[:, 3:6] if a.shape[1] >= 6 else None)
    import open3d as o3d
    pc = o3d.io.read_point_cloud(p)
    return np.asarray(pc.points), (np.asarray(pc.normals) if pc.has_normals() else None)


def orient_consistently(pcd, k=15, target=20_000):
    """Point every estimated normal out of the object. Open3D does this point by point (half an hour on a
    quarter-million points), so a thinned copy is oriented and each point takes its nearest sample's side."""
    import open3d as o3d
    from scipy.spatial import cKDTree
    n = len(pcd.points)
    if n <= target:
        pcd.orient_normals_consistent_tangent_plane(k)
        return
    thin = pcd.uniform_down_sample(n // target)          # every k-th point, normals included
    thin.orient_normals_consistent_tangent_plane(k)
    ref = np.asarray(thin.normals)
    j = cKDTree(np.asarray(thin.points)).query(np.asarray(pcd.points))[1]
    nn = np.asarray(pcd.normals).copy()
    nn[(nn * ref[j]).sum(1) < 0] *= -1
    pcd.normals = o3d.utility.Vector3dVector(nn)


def poisson_mesh(points, normals=None, depth=10, toward=None, keep_within=None, normals_k=30):
    """Screened Poisson surface from a dense scan (terrestrial laser or photogrammetry).

    Poisson closes every hole, which is surface the scanner never saw. Each vertex therefore gets its
    distance to the nearest scanned point; vertices farther than `keep_within` (default 3 x the median
    point spacing) are cut away, and the rest are coloured by that distance.
    """
    import open3d as o3d
    from scipy.spatial import cKDTree
    off = offset_for(points)
    local = points - off
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(local))
    if normals is not None:
        pcd.normals = o3d.utility.Vector3dVector(np.asarray(normals, float))
    pcd, _ = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=3.0)
    tree = cKDTree(np.asarray(pcd.points))
    spacing = float(np.median(tree.query(np.asarray(pcd.points), k=2)[0][:, 1]))
    if normals is None:
        pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=6 * spacing, max_nn=normals_k))
        if toward is not None:
            pcd.orient_normals_towards_camera_location(np.asarray(toward, float) - off)
        else:
            orient_consistently(pcd)
    mesh, _ = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth, n_threads=1)
    # One thread: in Open3D 0.19 the threaded solver crashed at random, and linear_fit=True crashed every time.
    v = np.asarray(mesh.vertices)
    f = np.asarray(mesh.triangles)
    gap = tree.query(v)[0]
    keep_within = 3 * spacing if keep_within is None else keep_within
    keep = gap <= keep_within
    fk = f[keep[f].all(1)]
    used = np.unique(fk)
    remap = np.full(len(v), -1)
    remap[used] = np.arange(len(used))
    return v[used] + off, remap[fk], gap[used], {'spacing_m': spacing, 'keep_within_m': keep_within,
                                                   'depth': depth, 'cut_vertices': int((~keep).sum()),
                                                   'normals': 'from the scan' if normals is not None else
                                                   ('towards ' + str(list(toward)) if toward is not None else 'estimated')}


def cmd_mesh3d(a):
    pts, normals = read_points(a.scan)
    toward = [float(t) for t in a.toward.split(',')] if a.toward else None
    xyz, faces, gap, info = poisson_mesh(pts, normals, depth=a.depth, toward=toward, keep_within=a.keep_within)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rgb = confidence_colours(gap, full_at=max(info['keep_within_m'], 1e-6))
    m = write_mesh(out / 'scan_mesh', xyz, faces, rgb, gap, a.formats.split(','), a.srs,
                   note='surface.py Poisson mesh; gap_m = metres to the nearest scanned point')
    rec = {'scan': str(a.scan), 'sha256': sha256(a.scan), 'points': int(len(pts)), **info, 'mesh': m,
           'versions': versions()}
    (out / 'run.json').write_text(json.dumps(rec, indent=1))
    print(f'{len(pts)} points, spacing {info["spacing_m"] * 1000:.1f} mm -> {m["triangles"]} triangles '
          f'({info["cut_vertices"]} vertices cut: no scan within {info["keep_within_m"] * 1000:.1f} mm)')


# Commands -------------------------------------------------------------------------------------------
def fingerprint(settings, resolution):
    """The settings that shape the surface, hashed: equal fingerprints = cases made the same way."""
    keep = {k: settings[k] for k in ('ground', 'noise', 'min_points')}
    keep['resolution'] = resolution
    if settings['ground'] in ('smrf', 'csf'):
        keep[settings['ground']] = settings[settings['ground']]
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:12]


def cmd_run(a):
    cases = load_cases(a.cases)
    if a.only:
        cases = [c for c in cases if c['name'] in a.only]
        if not cases:
            raise SystemExit(f'no case named {a.only}')
    if a.dry_run:
        for c in cases:
            print(json.dumps(c, indent=1))
        return
    out = Path(a.out or Path(a.cases).parent / 'surfaces')
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for c in cases:
        r = run_case(c, out)
        rows.append({'case': c['name'], 'ground': c['ground'], 'resolution_m': r['resolution_m'],
                     'ground_points_per_m2': r['ground_points_per_m2'], **r['counts'], 'seconds': r['seconds'],
                     'settings_fingerprint': fingerprint(c, r['resolution_m'])})
    table = out / 'cases.csv'
    old = []
    if table.exists():
        with open(table, newline='') as f:
            old = [r for r in csv.DictReader(f) if r['case'] not in {x['case'] for x in rows}]
    with open(table, 'w', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        for r in old + rows:
            wr.writerow({k: r.get(k, '') for k in rows[0]})
    same = len({r['settings_fingerprint'] for r in rows}) == 1
    print(f'{len(rows)} case(s) -> {table}' + ('' if same or len(rows) == 1 else
          '\nNote: the cases were not all made with the same settings (see settings_fingerprint in the table).'))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run', help='DTM, relief and mesh for every case study in a settings file')
    r.add_argument('cases')
    r.add_argument('-o', '--out')
    r.add_argument('--only', nargs='+')
    r.add_argument('--dry-run', action='store_true')
    m = sub.add_parser('mesh3d', help='a true 3D mesh from a dense close-range scan')
    m.add_argument('scan')
    m.add_argument('-o', '--out', required=True)
    m.add_argument('--depth', type=int, default=10, help='Poisson octree depth: 10 ~ 1/1000 of the object size')
    m.add_argument('--toward', help='x,y,z the scanner or camera stood at, to point normals outwards')
    m.add_argument('--keep-within', type=float, help='cut surface farther than this (m) from any scanned point')
    m.add_argument('--formats', default='ply,obj')
    m.add_argument('--srs', default='')
    a = p.parse_args(argv)
    {'run': cmd_run, 'mesh3d': cmd_mesh3d}[a.cmd](a)


if __name__ == '__main__':
    main()
