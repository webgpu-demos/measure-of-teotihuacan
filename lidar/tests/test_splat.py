"""Tests on the synthetic site only. Run: .venv/bin/python -m pytest lidar/tests/test_splat.py -q"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import splat as sp  # noqa: E402
from synth_lidar import ORIGIN, Site  # noqa: E402


def random_rotations(n, seed=0):
    rng = np.random.default_rng(seed)
    q, r = np.linalg.qr(rng.normal(size=(n, 3, 3)))
    q *= np.sign(np.diagonal(r, axis1=1, axis2=2))[:, None, :]
    q[np.linalg.det(q) < 0, :, 0] *= -1
    return q


def half_turns():
    """180-degree turns, where the simplest matrix-to-quaternion formulas lose the axis signs."""
    axes = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0], [1, -1, 0], [0, 1, -1], [1, -1, 1]], float)
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    return np.stack([2 * np.outer(a, a) - np.eye(3) for a in axes])


@pytest.mark.parametrize('R', [random_rotations(2000), half_turns()], ids=['random', 'half-turns'])
def test_quaternion_round_trip(R):
    q = sp.quat_from_matrix(R)
    assert np.allclose(np.linalg.norm(q, axis=1), 1)
    assert np.allclose(sp.matrix_from_quat(q), R, atol=1e-9)


@pytest.fixture(scope='module')
def site():
    s = Site(size=120.0, seed=3)
    return s, s.survey(density=10.0)


def bare(site_pts, s):
    """Ground returns in the open: no house or tree over them, away from the cut edges of the site."""
    x, y = site_pts['x'], site_pts['y']
    m = (site_pts['cls'] == 2) & (s.house_at(x, y) == 0) & (s.crown_at(x, y) == 0)
    return m & (x > 3) & (y > 3) & (x < s.size - 3) & (y < s.size - 3)


def points_dict(p, m):
    return {'xyz': np.column_stack([p['x'][m], p['y'][m], p['z'][m]]), 'cls': p['cls'][m].astype(np.uint8),
            'intensity': np.zeros(int(m.sum()), np.float32)}


def true_normals(s, x, y, h=0.05):
    gx = (s.ground(x + h, y) - s.ground(x - h, y)) / (2 * h)
    gy = (s.ground(x, y + h) - s.ground(x, y - h)) / (2 * h)
    n = np.column_stack([-gx, -gy, np.ones_like(x)])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def test_normals_follow_the_true_ground(site):
    s, p = site
    m = bare(p, s)
    pts = points_dict(p, m)
    out = sp.make_splats(pts, yup=False, isolated=np.inf)
    xyz = pts['xyz']
    n_true = true_normals(s, xyz[:, 0], xyz[:, 1])
    angle = np.degrees(np.arccos(np.clip((out['normal'] * n_true).sum(1), -1, 1)))

    def smooth(r):                                          # skip the breaks of slope (talud tops and feet)
        ok = np.ones(len(xyz), bool)
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
            ok &= (true_normals(s, xyz[:, 0] + dx, xyz[:, 1] + dy) * n_true).sum(1) > np.cos(np.radians(3))
        return ok
    flat = smooth(1.0)                                      # measured: median 2.0, p95 4.9 (3 cm sensor noise)
    assert np.median(angle[flat]) < 3.0
    assert np.percentile(angle[flat], 95) < 7.0
    steep = smooth(0.5) & (n_true[:, 2] < np.cos(np.radians(40)))    # the 63-degree taluds; measured median 0.6
    assert steep.sum() > 1000 and np.median(angle[steep]) < 2.0


def coverage_from_above(out, grid_xy, reach=3.0):
    """Alpha a top-down orthographic view would show at each grid point: 1 - prod(1 - alpha_i)."""
    from scipy.spatial import cKDTree
    R = sp.matrix_from_quat(out['rot'].astype(float))
    S = out['scale'].astype(float)
    cov = np.einsum('nij,nj,nkj->nik', R, S ** 2, R)[:, :2, :2]
    inv = np.linalg.inv(cov)
    centres = out['xyz'][:, :2].astype(float)
    tree = cKDTree(centres)
    transparent = np.ones(len(grid_xy))
    for i, nbrs in enumerate(tree.query_ball_point(grid_xy, reach * S[:, :2].max())):
        nbrs = np.asarray(nbrs, int)
        d = grid_xy[i] - centres[nbrs]
        alpha = out['opacity'][nbrs] * np.exp(-0.5 * np.einsum('ni,nij,nj->n', d, inv[nbrs], d))
        transparent[i] = np.prod(1 - alpha)
    return 1 - transparent


def coverage_of(pts, step):
    out = sp.make_splats(pts, yup=False, isolated=np.inf)
    lo = pts['xyz'].min(0)[:2] - out['offset'][:2] + 10
    g = np.stack(np.meshgrid(np.arange(lo[0], lo[0] + 30, step), np.arange(lo[1], lo[1] + 30, step)), -1)
    return coverage_from_above(out, g.reshape(-1, 2)), out


def dem_points(h=1.5, size=60):
    gx, gy = np.meshgrid(np.arange(0, size, h), np.arange(0, size, h))
    xyz = np.column_stack([gx.ravel(), gy.ravel(), (0.02 * gx + 0.01 * gy).ravel()])
    return {'xyz': xyz, 'cls': np.full(len(xyz), 2, np.uint8), 'intensity': np.zeros(len(xyz))}


def test_random_returns_leave_almost_no_holes(site):
    """Seen straight down, the worst case: random returns leave real gaps between points, so the splat size
    goes up to 1.0 x spacing. Measured: mean 0.998, 1st percentile 0.96 (at 0.8 it was 0.982 and 0.74)."""
    s, p = site
    c, out = coverage_of(points_dict(p, bare(p, s)), 0.1)
    assert out['regularity'] == pytest.approx(0.5, abs=0.05) and out['coverage'] == pytest.approx(1.0, abs=0.02)
    assert c.mean() > 0.995
    assert np.percentile(c, 1) > 0.9


def test_a_regular_grid_is_opaque():
    """A DEM's cells as points (INEGI's 1.5 m models): solid from above at 0.8 x spacing. Measured: 0.998."""
    c, out = coverage_of(dem_points(), 0.15)
    assert out['regularity'] > 0.95 and out['coverage'] == pytest.approx(0.8, abs=0.02)
    assert c.mean() > 0.995 and c.min() > 0.99


def test_splats_are_thin_on_ground_and_round_in_crowns(site):
    s, p = site
    m = np.isin(p['cls'], (2, 5)) & (p['x'] > 3) & (p['y'] > 3) & (p['x'] < s.size - 3) & (p['y'] < s.size - 3)
    pts = points_dict(p, m)
    out = sp.make_splats(pts, yup=False, isolated=np.inf)
    flatness = out['scale'][:, 2] / out['scale'][:, 1]
    ground, crown = pts['cls'] == 2, pts['cls'] == 5
    assert np.median(flatness[ground]) < 0.25
    assert np.median(flatness[crown]) > 2 * np.median(flatness[ground])


@pytest.fixture(scope='module')
def laz(tmp_path_factory):
    folder = tmp_path_factory.mktemp('synthetic')
    s = Site(size=80.0, seed=5)
    s.write(folder, density=6.0, classified=True)
    return s, folder / 'survey.laz'


def test_cli_writes_a_valid_3dgs_ply(laz, tmp_path):
    s, path = laz
    out = tmp_path / 'site.ply'
    sp.main([str(path), '-o', str(out), '--max-points', '20000'])
    v = sp.read_ply(out)
    assert list(v.dtype.names) == sp.PLY_FIELDS
    assert 15000 <= len(v) <= 20000
    for f in sp.PLY_FIELDS:
        assert np.isfinite(v[f]).all(), f
    q = np.column_stack([v[f'rot_{i}'] for i in range(4)])
    assert np.allclose(np.linalg.norm(q, axis=1), 1, atol=1e-5)
    rgb = 0.5 + sp.SH_C0 * np.column_stack([v[f'f_dc_{i}'] for i in range(3)])
    assert rgb.min() >= -1e-5 and rgb.max() <= 1 + 1e-5
    meta = json.loads(out.with_suffix('.json').read_text())
    biggest = 0.8 * 4 * np.sqrt(2) * meta['median_spacing_m']     # coverage x spacing cap x anisotropy
    assert np.exp(v['scale_0']).max() <= biggest * 1.01 and np.exp(v['scale_2']).min() > 1e-4
    assert meta['splats'] == len(v) and meta['axes'] == 'x east, y up, z south'
    assert np.ptp(v['y']) < 60                              # y is height: no bird returns (class 7) left


def test_ground_only_splats_sit_on_the_true_ground(laz, tmp_path):
    """--classes 2 keeps the bare earth, and the sidecar's offset maps every splat back onto it."""
    s, path = laz
    out = tmp_path / 'ground.ply'
    sp.main([str(path), '-o', str(out), '--classes', '2'])
    v = sp.read_ply(out)
    meta = json.loads(out.with_suffix('.json').read_text())
    enu = np.column_stack([v['x'], v['y'], v['z']]).astype(float) @ sp.ENU_TO_YUP + meta['offset']
    x, y = enu[:, 0] - ORIGIN[0], enu[:, 1] - ORIGIN[1]
    err = enu[:, 2] - s.ground(x, y)
    assert np.abs(err).max() < 0.2                          # sensor noise is 3 cm; roofs and crowns are gone
