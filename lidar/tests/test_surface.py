"""The surface pipeline, scored against made-up sites whose true surface is known.

Run with the lidar environment (PDAL, GDAL, scipy, Open3D):  python -m pytest lidar/tests -q
"""
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip('scipy')
pytest.importorskip('laspy')
import surface as S                                     # noqa: E402
from synth_lidar import Facade, Site                    # noqa: E402

try:
    S.tool('pdal'), S.tool('gdal_translate'), S.tool('gdaldem')
except SystemExit:
    pytest.skip('PDAL and GDAL command-line tools not found', allow_module_level=True)

BOUNDS = [512000, 2176000, 512200, 2176200]
CASES = f'''
[defaults]
resolution = 0.5
srs = "EPSG:32614"
bounds = {BOUNDS}
hash_inputs = false

[[case]]
name = "fresh"
input = "survey.laz"
ground = "smrf"

[[case]]
name = "vendor"
input = "survey_classified.laz"
ground = "delivered"
mesh_formats = ["ply"]
'''


@pytest.fixture(scope='module')
def site(tmp_path_factory):
    d = tmp_path_factory.mktemp('site')
    Site().write(d)
    Site().write(d, classified=True, name='survey_classified.laz')
    (d / 'cases.toml').write_text(CASES)
    S.main(['run', str(d / 'cases.toml'), '-o', str(d / 'out')])
    truth = np.load(d / 'truth.npy')
    n = truth.shape[0]
    c = (np.arange(n) + 0.5) * 0.5
    gx, gy = np.meshgrid(c, c[::-1])
    masks = dict(np.load(d / 'truth_masks.npz'))
    from scipy import ndimage
    masks['house_inside'] = ndimage.binary_erosion(masks['house'], iterations=2)
    masks['altar_top'] = ndimage.binary_erosion(masks['altar'], iterations=2)
    masks['open'] = ~(masks['house'] | masks['tree'] | masks['pyramid'] | masks['altar'] | masks['walls'])
    masks['walls_clear'] = masks['walls'] & ~masks['tree']
    return {'dir': d, 'out': d / 'out', 'truth': truth, 'natural': Site().natural(gx, gy), 'm': masks}


def rmse(z, truth, mask):
    e = (z - truth)[mask]
    e = e[np.isfinite(e)]
    return float(np.sqrt(np.mean(e ** 2)))


def kept(z, site, mask):
    """Median height above the natural ground: how much of a feature the surface kept."""
    return float(np.nanmedian((z - site['natural'])[site['m'][mask]]))


@pytest.mark.parametrize('case', ['fresh', 'vendor'])
def test_surface_matches_the_true_ground(site, case):
    z = S.read_raster(site['out'] / case / 'dtm.tif')
    t, m = site['truth'], site['m']
    assert rmse(z, t, m['open']) < 0.04
    assert rmse(z, t, m['house_inside']) < 0.05          # houses removed, ground interpolated beneath
    assert rmse(z, t, m['tree'] & ~m['house']) < 0.12    # from the few pulses that reach the ground
    assert rmse(z, t, m['pyramid']) < 0.2                # the taluds are what limit this, at 0.5 m cells


@pytest.mark.parametrize('case', ['fresh', 'vendor'])
def test_ancient_features_survive_the_ground_filter(site, case):
    z = S.read_raster(site['out'] / case / 'dtm.tif')
    assert kept(z, site, 'walls_clear') > 0.3            # 0.4 m walls
    assert kept(z, site, 'altar_top') > 1.4              # 1.5 m altar, the size of a small house
    top = np.nanmax((z - site['natural'])[site['m']['pyramid']])
    assert abs(top - 16.0) < 0.3


def test_connecting_every_point_is_what_goes_wrong(site, tmp_path):
    """The plain approach: every return, triangles between them. Roofs, canopy and birds become ground."""
    naive = tmp_path / 'naive.bil'
    S.pdal_pipeline([{'type': 'readers.las', 'filename': str(site['dir'] / 'survey.laz')},
                     {'type': 'filters.delaunay'},
                     {'type': 'filters.faceraster', **S.grid_opts(S.grid(BOUNDS, 0.5), 0.5)},
                     {'type': 'writers.raster', 'filename': str(naive), 'gdaldriver': 'ENVI'}], tmp_path / 'p.json')
    z = S.band(naive)
    good = S.read_raster(site['out'] / 'fresh' / 'dtm.tif')
    t, m = site['truth'], site['m']
    assert rmse(z, t, m['house_inside']) > 2.0 > 20 * rmse(good, t, m['house_inside'])
    assert rmse(z, t, m['tree'] & ~m['house']) > 2.0
    assert np.nanmax((z - site['natural'])[m['pyramid']]) > 25       # a bird over the pyramid


def test_pdal_default_settings_cut_the_altar(site, tmp_path):
    """Why the settings are recorded per case: PDAL's own SMRF defaults flatten a 1.5 m platform."""
    cfg = CASES.split('[[case]]')[0] + f'''
[[case]]
name = "pdal-defaults"
input = "{site['dir'] / 'survey.laz'}"
ground = "smrf"
smrf = {{ slope = 0.15, window = 18.0 }}
mesh = false
relief = false
'''
    (tmp_path / 'c.toml').write_text(cfg)
    S.main(['run', str(tmp_path / 'c.toml'), '-o', str(tmp_path / 'out')])
    z = S.read_raster(tmp_path / 'out' / 'pdal-defaults' / 'dtm.tif')
    assert kept(z, site, 'altar_top') < 1.0
    removed = S.read_raster(tmp_path / 'out' / 'pdal-defaults' / 'removed.tif')
    assert np.nanmedian(removed[site['m']['altar_top']]) > 0.5         # and removed.tif shows it


def test_removed_raster_shows_houses_not_the_altar(site):
    removed = S.read_raster(site['out'] / 'fresh' / 'removed.tif')
    m = site['m']
    assert np.nanmedian(removed[m['house_inside']]) > 2.5
    assert np.nanmedian(removed[m['altar_top']]) == 0
    assert np.nanmedian(removed[m['open']]) == 0


def test_estimated_cells_are_flagged(site):
    pts = S.read_raster(site['out'] / 'fresh' / 'points.tif')
    gap = S.read_raster(site['out'] / 'fresh' / 'gap.tif')
    m = site['m']
    assert np.all(pts[m['house_inside']] == 0) and np.all(gap[m['house_inside']] > 0)
    assert np.mean(pts[m['open']] > 0) > 0.85              # ~10 pts/m2 in 0.25 m2 cells: some are empty
    assert np.all(gap[pts > 0] == 0)


def read_ply(path):
    raw = Path(path).read_bytes()
    end = raw.index(b'end_header\n') + len(b'end_header\n')
    head = raw[:end].decode()
    nv = int(re.search(r'element vertex (\d+)', head).group(1))
    nf = int(re.search(r'element face (\d+)', head).group(1))
    off = np.array([float(x) for x in re.search(r'comment offset (\S+) (\S+) (\S+)', head).groups()])
    vt = np.dtype([('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('red', 'u1'), ('green', 'u1'), ('blue', 'u1'),
                   ('gap_m', '<f4')])
    v = np.frombuffer(raw, vt, nv, end)
    f = np.frombuffer(raw, np.dtype([('n', 'u1'), ('i', '<i4', (3,))]), nf, end + nv * vt.itemsize)
    assert end + nv * vt.itemsize + nf * 13 == len(raw)
    return head, v, f['i'], off


def test_mesh_is_the_dtm_to_the_millimetre(site):
    head, v, faces, off = read_ply(site['out'] / 'fresh' / 'surface.ply')
    z = S.read_raster(site['out'] / 'fresh' / 'dtm.tif')
    assert 'crs EPSG:32614' in head
    assert len(v) == np.isfinite(z).sum()
    xyz = np.column_stack([v['x'], v['y'], v['z']]).astype(np.float64) + off
    col = np.round((xyz[:, 0] - BOUNDS[0]) / 0.5 - 0.5).astype(int)
    row = np.round((BOUNDS[3] - xyz[:, 1]) / 0.5 - 0.5).astype(int)
    assert np.abs(xyz[:, 0] - (BOUNDS[0] + (col + 0.5) * 0.5)).max() < 0.001
    assert np.abs(xyz[:, 2] - z[row, col]).max() < 0.001
    a, b, c = xyz[faces[:, 0]], xyz[faces[:, 1]], xyz[faces[:, 2]]
    assert (np.cross(b - a, c - a)[:, 2] > 0).all()           # every triangle faces up
    orange = (v['red'].astype(int) - v['blue']) > 100
    assert orange[v['gap_m'] > 2.5].all() and not orange[v['gap_m'] == 0].any()
    obj = (site['out'] / 'fresh' / 'surface.obj').read_text().splitlines()
    assert obj[0].startswith('# offset') and sum(l.startswith('f ') for l in obj) == len(faces)
    assert not (site['out'] / 'vendor' / 'surface.obj').exists()     # per-case setting respected


def test_every_case_is_recorded_and_repeatable(site, tmp_path):
    rec = json.loads((site['out'] / 'fresh' / 'run.json').read_text())
    assert rec['settings']['ground'] == 'smrf' and rec['settings']['smrf']['slope'] == S.DEFAULTS['smrf']['slope']
    assert {'classify', 'dtm', 'dsm'} <= set(rec['pipelines'])
    assert 'pdal' in rec['versions'] and rec['resolution_m'] == 0.5
    assert all(len(o['sha256']) == 64 for o in rec['outputs'].values())
    rows = (site['out'] / 'cases.csv').read_text().splitlines()
    assert rows[0].startswith('case,') and {r.split(',')[0] for r in rows[1:]} == {'fresh', 'vendor'}
    shutil.copy(site['dir'] / 'survey.laz', tmp_path / 'survey.laz')
    (tmp_path / 'cases.toml').write_text(CASES.replace('survey_classified.laz', 'survey.laz'))
    S.main(['run', str(tmp_path / 'cases.toml'), '-o', str(tmp_path / 'out'), '--only', 'fresh'])
    again = json.loads((tmp_path / 'out' / 'fresh' / 'run.json').read_text())
    assert again['outputs']['dtm.tif']['sha256'] == rec['outputs']['dtm.tif']['sha256']


def test_auto_resolution_follows_the_ground_density(site, tmp_path):
    (tmp_path / 'c.toml').write_text(f'''
[defaults]
bounds = {BOUNDS}
hash_inputs = false
mesh = false
relief = false
[[case]]
name = "auto"
input = "{site['dir'] / 'survey_classified.laz'}"
''')
    S.main(['run', str(tmp_path / 'c.toml'), '-o', str(tmp_path / 'out')])
    rec = json.loads((tmp_path / 'out' / 'auto' / 'run.json').read_text())
    assert 9 <= rec['ground_points_per_m2'] <= 11
    assert rec['resolution_m'] == 0.45                      # sqrt(2 / 10) = 0.447, rounded up to 0.05


def test_fingerprint_ignores_names_areas_and_outputs():
    base = S.merge(S.DEFAULTS, {'name': 'a', 'ground': 'smrf'})
    other = S.merge(base, {'name': 'b', 'bounds': [0, 0, 1, 1], 'mesh': False, 'note': 'x', 'csf': {'rigidness': 3}})
    assert S.fingerprint(base, 0.5) == S.fingerprint(other, 0.5)
    assert S.fingerprint(base, 0.5) != S.fingerprint(base, 1.0)
    assert S.fingerprint(base, 0.5) != S.fingerprint(S.merge(base, {'smrf': {'slope': 0.15}}), 0.5)
    assert S.fingerprint(base, 0.5) != S.fingerprint(S.merge(base, {'ground': 'delivered'}), 0.5)


@pytest.mark.parametrize('text, message', [
    ('[defaults]\nresolutoin = 1\n', 'unknown setting'),
    ('[[case]]\nname = "a"\ninput = "nothing-*.laz"\n', 'no files match'),
    ('[[case]]\nname = "a/b"\ninput = "x.laz"\n', 'case name'),
    ('[[case]]\nname = "a"\ninput = "x.laz"\nground = "magic"\n', 'ground must be'),
])
def test_settings_file_mistakes_are_caught(tmp_path, text, message):
    (tmp_path / 'x.laz').write_bytes(b'')
    (tmp_path / 'c.toml').write_text(text)
    with pytest.raises(SystemExit, match=message):
        S.load_cases(tmp_path / 'c.toml')


# Close-range scans --------------------------------------------------------------------------------------
@pytest.fixture(scope='module')
def facade():
    pytest.importorskip('open3d')
    from scipy.spatial import cKDTree
    f = Facade()
    truth, _ = f.sample(2_000_000)
    pts, normals = f.sample(200_000, sigma=0.001)
    return {'truth': cKDTree(truth), 'truth_pts': truth, 'pts': pts, 'normals': normals}


def on_surface(v, faces, n=200_000, seed=0):
    r = np.random.default_rng(seed)
    a, b, c = v[faces[:, 0]], v[faces[:, 1]], v[faces[:, 2]]
    area = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    k = r.choice(len(faces), n, p=area / area.sum())
    u, w = r.random(n), r.random(n)
    flip = u + w > 1
    u[flip], w[flip] = 1 - u[flip], 1 - w[flip]
    return a[k] + u[:, None] * (b[k] - a[k]) + w[:, None] * (c[k] - a[k])


def accuracy(fc, v, faces):
    """Share of the mesh within 5 mm of the true surface, and share of the true surface within 5 mm of the mesh."""
    from scipy.spatial import cKDTree
    sp = on_surface(v, faces, n=1_000_000)
    within = float(np.mean(fc['truth'].query(sp)[0] < 0.005))
    covered = float(np.mean(cKDTree(sp).query(fc['truth_pts'][::10])[0] < 0.005))
    return within, covered


def test_connecting_every_point_fails_on_carving(facade):
    from scipy.spatial import Delaunay
    p = facade['pts']
    within, _ = accuracy(facade, p, Delaunay(p[:, [0, 2]]).simplices)     # triangles as seen from the front
    assert within < 0.6


@pytest.mark.parametrize('given', [True, False], ids=['normals-from-scan', 'normals-estimated'])
def test_poisson_mesh_follows_the_carving(facade, given):
    v, faces, gap, info = S.poisson_mesh(facade['pts'], facade['normals'] if given else None, depth=10)
    within, covered = accuracy(facade, v, faces)
    assert within > 0.99 and covered > 0.98
    assert gap.max() <= info['keep_within_m'] + 1e-9                # surface the scan never saw is cut


def test_mesh3d_command(facade, tmp_path):
    scan = tmp_path / 'scan.xyz'
    np.savetxt(scan, facade['pts'][::4], fmt='%.4f')
    S.main(['mesh3d', str(scan), '-o', str(tmp_path / 'm'), '--depth', '9', '--formats', 'ply'])
    rec = json.loads((tmp_path / 'm' / 'run.json').read_text())
    assert rec['normals'] == 'estimated' and rec['mesh']['triangles'] > 10_000
    head, v, faces, off = read_ply(tmp_path / 'm' / 'scan_mesh.ply')
    xyz = np.column_stack([v['x'], v['y'], v['z']]).astype(np.float64) + off
    assert np.median(facade['truth'].query(xyz)[0]) < 0.002          # float32 + offset keeps millimetres
