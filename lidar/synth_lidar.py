"""A made-up lidar survey with a known bare earth, and a made-up close-range scan of a carved facade,
for testing the surface pipeline.

The site is laid out on the city grid (15.5 degrees east of north) and holds what the real valley holds:
ancient features that belong to the bare earth and must survive ground filtering (a stepped pyramid with
steep taluds, a small altar platform, a compound of low walls), and modern things that must not (flat-roofed
houses, trees). Pulses hit roofs, canopy or ground; under trees only some pulses reach the ground. A few
low and high outliers (multipath, birds) are mixed in, as in real deliveries.

Everything ancient is part of `ground(x, y)`, the truth the pipeline is scored against. Houses and tree
crowns are kept as footprints so the tests can score each kind of place separately.
"""
import json
import math
from pathlib import Path

import numpy as np

GRID_DEG = 15.5
ORIGIN = (512000.0, 2176000.0)          # made-up UTM-like origin; the site is synthetic
BASE_Z = 2280.0


def rot(x, y, cx, cy):
    """Site (x east, y north) -> the feature's own grid frame (u, v) around (cx, cy)."""
    a = math.radians(GRID_DEG)
    dx, dy = x - cx, y - cy
    return dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)


class Site:
    def __init__(self, size=200.0, seed=1):
        self.size = size
        self.rng = np.random.default_rng(seed)
        s = size / 200.0                                  # features are placed for a 200 m site and scaled
        self.pyramid = dict(cx=95 * s, cy=110 * s, tiers=4, base_half=25.0, tier_h=4.0, talud=2.0, terrace=3.0)
        self.altar = dict(cx=30 * s, cy=35 * s, half=5.0, h=1.5, edge=0.5)
        self.walls = dict(cx=160 * s, cy=50 * s, half_u=18.0, half_v=14.0, h=0.4, width=1.0, inner=(-4.0, 6.0))
        r = np.random.default_rng(seed + 100)
        self.houses = []
        for k in range(6):
            self.houses.append(dict(cx=(25 + 28 * k) * s, cy=178 * s + r.uniform(-6, 6), half_u=r.uniform(4, 6),
                                    half_v=r.uniform(4, 6), h=r.uniform(3, 6)))
        self.trees = []
        spots = [(60, 150), (75, 160), (140, 130), (150, 95), (170, 100), (45, 90), (115, 60), (120, 150),
                 (180, 150), (20, 130)]
        spots += [(95 + 14 * math.cos(t), 110 + 14 * math.sin(t)) for t in (0.3, 1.9, 3.6, 5.1)]   # on the pyramid
        spots += [(150, 40), (172, 60)]                                                            # over the walls
        for (x, y) in spots:
            self.trees.append(dict(cx=x * s, cy=y * s, r=r.uniform(3, 5), h=r.uniform(6, 10)))

    # The truth -------------------------------------------------------------------------------
    def natural(self, x, y):
        return BASE_Z + 0.012 * x - 0.006 * y + 1.2 * np.sin(x / 55.0) * np.cos(y / 40.0)

    def pyramid_h(self, x, y):
        p = self.pyramid
        u, v = rot(x, y, p['cx'], p['cy'])
        r = np.maximum(np.abs(u), np.abs(v))
        h = np.zeros_like(r)
        for k in range(p['tiers']):
            outer = p['base_half'] - k * (p['talud'] + p['terrace'])
            h += p['tier_h'] * np.clip((outer - r) / p['talud'], 0, 1)
        return h

    def altar_h(self, x, y):
        a = self.altar
        u, v = rot(x, y, a['cx'], a['cy'])
        r = np.maximum(np.abs(u), np.abs(v))
        return a['h'] * np.clip((a['half'] - r) / a['edge'], 0, 1)

    def walls_h(self, x, y):
        w = self.walls
        u, v = rot(x, y, w['cx'], w['cy'])
        d = np.full_like(u, np.inf)
        inside_u, inside_v = np.abs(u) <= w['half_u'] + w['width'], np.abs(v) <= w['half_v'] + w['width']
        for line in (w['half_u'], -w['half_u']):                       # walls running along v
            d = np.where(inside_v, np.minimum(d, np.abs(u - line)), d)
        for line in (w['half_v'], -w['half_v'], w['inner'][1]):       # walls running along u
            d = np.where(inside_u, np.minimum(d, np.abs(v - line)), d)
        d = np.where(inside_v & (v > w['inner'][1]), np.minimum(d, np.abs(u - w['inner'][0])), d)   # a cross wall
        half = w['width'] / 2
        return w['h'] * np.clip((half + 0.1 - d) / 0.2, 0, 1)

    def ground(self, x, y):
        """The bare earth, ancient features included: what the DTM should reproduce."""
        return self.natural(x, y) + self.pyramid_h(x, y) + self.altar_h(x, y) + self.walls_h(x, y)

    def house_at(self, x, y):
        """Roof height above ground where a house stands, else 0."""
        out = np.zeros_like(x)
        for hs in self.houses:
            u, v = rot(x, y, hs['cx'], hs['cy'])
            out = np.where((np.abs(u) <= hs['half_u']) & (np.abs(v) <= hs['half_v']), hs['h'], out)
        return out

    def crown_at(self, x, y):
        """Canopy height above ground where a tree crown is, else 0 (a dome)."""
        out = np.zeros_like(x)
        for t in self.trees:
            r = np.hypot(x - t['cx'], y - t['cy'])
            dome = t['h'] * np.sqrt(np.clip(1 - (r / t['r']) ** 2, 0, 1)) * 0.4 + t['h'] * 0.6
            out = np.where(r <= t['r'], np.maximum(out, dome), out)
        return out

    def masks(self, x, y):
        return {'house': self.house_at(x, y) > 0, 'tree': self.crown_at(x, y) > 0,
                'pyramid': self.pyramid_h(x, y) > 0.05, 'altar': self.altar_h(x, y) > 0.05,
                'walls': self.walls_h(x, y) > 0.2}

    # The survey -------------------------------------------------------------------------------
    def survey(self, density=10.0, ground_through_canopy=0.35, sigma=0.03):
        """Simulated returns: dict of arrays x, y, z, return_number, number_of_returns."""
        n = self.rng.poisson(density * self.size ** 2)
        x = self.rng.uniform(0, self.size, n)
        y = self.rng.uniform(0, self.size, n)
        g = self.ground(x, y)
        roof = self.house_at(x, y)
        crown = self.crown_at(x, y)
        xs, ys, zs, rn, nr, cl = [], [], [], [], [], []

        def add(m, z, r, k, c):
            xs.append(x[m]); ys.append(y[m]); zs.append(z); rn.append(np.full(m.sum(), r)); nr.append(np.full(m.sum(), k))
            cl.append(np.full(m.sum(), c))

        on_roof = roof > 0
        add(on_roof, g[on_roof] + roof[on_roof] + self.rng.normal(0, sigma, on_roof.sum()), 1, 1, 6)
        in_tree = ~on_roof & (crown > 0)
        through = in_tree & (self.rng.random(n) < ground_through_canopy)
        canopy_only = in_tree & ~through
        top = crown * self.rng.uniform(0.7, 1.0, n)
        add(canopy_only, g[canopy_only] + top[canopy_only], 1, 1, 5)
        add(through, g[through] + top[through], 1, 2, 5)
        add(through, g[through] + self.rng.normal(0, sigma, through.sum()), 2, 2, 2)
        bare = ~on_roof & ~in_tree
        add(bare, g[bare] + self.rng.normal(0, sigma, bare.sum()), 1, 1, 2)
        pts = {k: np.concatenate(v) for k, v in zip(('x', 'y', 'z', 'return_number', 'number_of_returns', 'cls'),
                                                     (xs, ys, zs, rn, nr, cl))}
        m = len(pts['x'])                                   # outliers: multipath below ground, birds above
        low = self.rng.choice(m, max(1, m // 10000), replace=False)
        pts['z'][low] -= self.rng.uniform(2, 10, len(low))
        high = self.rng.choice(m, max(1, m // 10000), replace=False)
        pts['z'][high] += self.rng.uniform(30, 60, len(high))
        pts['cls'][low], pts['cls'][high] = 7, 7
        return pts

    def write(self, folder, density=10.0, res=0.5, classified=False, name='survey.laz'):
        """Write the survey plus truth.npy (the bare earth on a res-metre grid), its masks and truth.json.

        classified=False leaves every point unclassified, as if fresh from the sensor; True writes the classes
        a vendor would deliver (2 ground, 5 vegetation, 6 building, 7 noise), as if already processed.
        """
        import laspy
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        pts = self.survey(density)
        header = laspy.LasHeader(point_format=6, version='1.4')
        header.scales = [0.001, 0.001, 0.001]
        header.offsets = [ORIGIN[0], ORIGIN[1], BASE_Z]
        las = laspy.LasData(header)
        las.x = pts['x'] + ORIGIN[0]
        las.y = pts['y'] + ORIGIN[1]
        las.z = pts['z']
        las.return_number = pts['return_number']
        las.number_of_returns = pts['number_of_returns']
        las.classification = pts['cls'].astype(np.uint8) if classified else np.zeros(len(pts['x']), np.uint8)
        las.write(folder / name)
        n = int(round(self.size / res))
        c = (np.arange(n) + 0.5) * res
        gx, gy = np.meshgrid(c, c[::-1])                    # row 0 = north edge, like a GeoTIFF
        np.save(folder / 'truth.npy', self.ground(gx, gy).astype(np.float32))
        masks = self.masks(gx, gy)
        np.savez_compressed(folder / 'truth_masks.npz', **masks)
        meta = {'origin': [ORIGIN[0], ORIGIN[1] + self.size], 'res': res, 'size': self.size, 'points': int(len(las.x)),
                'pyramid_height': self.pyramid['tiers'] * self.pyramid['tier_h'], 'altar_height': self.altar['h'],
                'wall_height': self.walls['h']}
        (folder / 'truth.json').write_text(json.dumps(meta, indent=1))
        return meta


class Facade:
    """A carved panel as a terrestrial scanner or photogrammetry would capture it, with the true surface.

    A 2 m x 1.2 m wall facing +y, a 4 cm raised band near the foot, a projecting head (a sphere 0.3 m
    across, standing 0.55 m proud, so its underside is an overhang) and a cornice whose underside is another.
    Lengths in metres; the panel sits at a made-up UTM position like the rest.
    """
    BAND = (0.15, 0.25, 0.04)          # z0, z1, depth
    CORNICE = (1.0, 1.1, 0.20)
    HEAD = (1.0, 0.25, 0.6, 0.30)      # cx, cy, cz, r
    W, H = 2.0, 1.2

    def __init__(self, seed=3):
        self.rng = np.random.default_rng(seed)

    def _pieces(self):
        """(area, sampler) for each visible part; samplers return (points, normals)."""
        r = self.rng
        W, H = self.W, self.H
        (b0, b1, bd), (c0, c1, cd), (hx, hy, hz, hr) = self.BAND, self.CORNICE, self.HEAD

        def plane(n, x0, x1, z0, z1, y=0.0, normal=(0, 1, 0)):
            p = np.column_stack([r.uniform(x0, x1, n), np.full(n, y), r.uniform(z0, z1, n)])
            return p, np.tile(normal, (n, 1)).astype(float)

        def hplane(n, x0, x1, y0, y1, z, normal):
            p = np.column_stack([r.uniform(x0, x1, n), r.uniform(y0, y1, n), np.full(n, z)])
            return p, np.tile(normal, (n, 1)).astype(float)

        def wall(n):
            p, nn = plane(n, 0, W, 0, H)
            keep = ~(((p[:, 2] >= b0) & (p[:, 2] <= b1)) | ((p[:, 2] >= c0) & (p[:, 2] <= c1)))
            keep &= np.hypot(p[:, 0] - hx, p[:, 2] - hz) > math.sqrt(hr ** 2 - hy ** 2)     # behind the head
            return p[keep], nn[keep]

        def head(n):
            v = r.normal(size=(n, 3))
            v /= np.linalg.norm(v, axis=1, keepdims=True)
            p = np.array([hx, hy, hz]) + hr * v
            keep = p[:, 1] > 0
            return p[keep], v[keep]

        return [
            (W * H, wall),
            (W * (b1 - b0), lambda n: plane(n, 0, W, b0, b1, bd)),
            (W * bd, lambda n: hplane(n, 0, W, 0, bd, b1, (0, 0, 1))),
            (W * bd, lambda n: hplane(n, 0, W, 0, bd, b0, (0, 0, -1))),
            (W * (c1 - c0), lambda n: plane(n, 0, W, c0, c1, cd)),
            (W * cd, lambda n: hplane(n, 0, W, 0, cd, c1, (0, 0, 1))),
            (W * cd, lambda n: hplane(n, 0, W, 0, cd, c0, (0, 0, -1))),     # the cornice's underside
            (4 * math.pi * hr ** 2, head),
        ]

    def sample(self, n, sigma=0.0):
        """About n points spread evenly over the visible surface, with their true normals."""
        pieces = self._pieces()
        total = sum(a for a, _ in pieces)
        pts, nrm = zip(*[f(max(1, int(round(n * a / total)))) for a, f in pieces])
        p, nn = np.concatenate(pts), np.concatenate(nrm)
        p = p + nn * self.rng.normal(0, sigma, (len(p), 1))
        return p + np.array([ORIGIN[0], ORIGIN[1], BASE_Z]), nn

    def write_scan(self, path, n=300_000, sigma=0.001, normals=False):
        """A scan as an .xyz text file (x y z, or x y z nx ny nz), the plainest export scanners offer."""
        p, nn = self.sample(n, sigma)
        np.savetxt(path, np.column_stack([p, nn]) if normals else p, fmt='%.4f')
        return len(p)


if __name__ == '__main__':
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else 'synthetic'
    print(Site().write(out))
