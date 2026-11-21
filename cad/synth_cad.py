"""Made-up AutoCAD drawings with a known unit of length, for testing the reader.

Buildings are laid out in a local grid frame (u east-ish, v north-ish, z up, metres) with
designed lengths in whole multiples of `unit` plus construction error. Each drawing is then
rotated onto the city grid, moved to UTM-like coordinates and written in its own units, so the
tests can check that the reader undoes all of that and that no coordinate leaks into its output.

`unit=None` draws the same buildings with every designed length scrambled (no unit at all).

The drawings deliberately include the things real excavation files contain that must not reach
the test: a hypothetical reconstruction drawn on a 1 m module, 2 x 2 m excavation units, survey
grid lines, contours, dimensions and text, curves, and a 3D solid the reader cannot open.
"""
import math

import ezdxf
import numpy as np
from ezdxf.acis import api as acis
from ezdxf.render import forms

UNITS = {'m': (ezdxf.units.M, 1.0), 'cm': (ezdxf.units.CM, 100.0), 'mm': (ezdxf.units.MM, 1000.0)}
ORIGIN = (516200.0, 2177150.0, 2290.0)  # near the city centre in UTM 14N, so leaks are easy to spot


class Drafter:
    def __init__(self, unit=0.83, seed=1, units='m', grid_deg=15.5, origin=ORIGIN, sigma=0.015, jitter=0.003):
        self.unit, self.sigma, self.jitter = unit, sigma, jitter
        self.rng = np.random.default_rng(seed)
        code, self.scale = UNITS[units]
        self.doc = ezdxf.new('R2010', setup=True)
        self.doc.units = code
        self.msp = self.doc.modelspace()
        self.grid_deg = grid_deg
        a = math.radians(-grid_deg)
        self.c, self.s = math.cos(a), math.sin(a)
        self.origin = origin
        self.truth = {}

    # Designed lengths ------------------------------------------------------------------
    def dim(self, k):
        """k units plus construction error, or (no unit) a length of similar size with no unit in it."""
        if self.unit is None:
            return float(k * 0.83 * math.exp(self.rng.uniform(math.log(0.8), math.log(1.25))))
        return float(k * self.unit + self.rng.normal(0, self.sigma))

    # Coordinates -------------------------------------------------------------------------
    def w(self, u, v, z=0.0, jit=True):
        """Local (u, v, z) in metres -> drawing coordinates."""
        if jit:
            u, v, z = (c + self.rng.normal(0, self.jitter) for c in (u, v, z))
        x = self.origin[0] + u * self.c - v * self.s
        y = self.origin[1] + u * self.s + v * self.c
        return (x * self.scale, y * self.scale, (self.origin[2] + z) * self.scale)

    def layer(self, name):
        if name not in self.doc.layers:
            self.doc.layers.add(name)
        return {'layer': name}

    # Primitives ----------------------------------------------------------------------------
    def line(self, layer, a, b):
        self.msp.add_line(self.w(*a), self.w(*b), dxfattribs=self.layer(layer))

    def rect(self, layer, u0, u1, v0, v1, z=0.0, thickness=0.0):
        """Closed LWPOLYLINE, optionally extruded (walls drawn with AutoCAD thickness)."""
        pts = [self.w(u, v, z) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
        attribs = self.layer(layer) | {'elevation': pts[0][2], 'thickness': thickness * self.scale}
        self.msp.add_lwpolyline([(p[0], p[1]) for p in pts], close=True, dxfattribs=attribs)

    def box_mesh(self, layer, u0, u1, v0, v1, z0, z1, inset=0.0, triangulate=False):
        """MESH prism; `inset` makes a talud (sloping sides), `triangulate` splits every quad."""
        bot = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
        top = [(u0 + inset, v0 + inset), (u1 - inset, v0 + inset), (u1 - inset, v1 - inset), (u0 + inset, v1 - inset)]
        verts = [self.w(u, v, z0) for u, v in bot] + [self.w(u, v, z1) for u, v in top]
        quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        faces = [t for q in quads for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))] if triangulate else quads
        mesh = self.msp.add_mesh(dxfattribs=self.layer(layer))
        with mesh.edit_data() as data:
            data.vertices = verts
            data.faces = faces


def temple(d):
    """A three-tier talud-tablero platform with a stair, balustrades and a reconstructed summit temple."""
    U = d.dim
    A, B = U(36), U(32)
    ht, hb, run, step = U(3), U(2), U(1), U(3)
    d.truth.update(base_u=A, base_v=B, talud_h=ht, tablero_h=hb)
    u0, u1, v0, v1, z = -A / 2, A / 2, -B / 2, B / 2, 0.0
    for tier in range(3):
        d.box_mesh('PLATAFORMA TALUD', u0, u1, v0, v1, z, z + ht, inset=run)
        z += ht
        u0, u1, v0, v1 = u0 + run, u1 - run, v0 + run, v1 - run
        d.box_mesh('PLATAFORMA TABLERO', u0, u1, v0, v1, z, z + hb, triangulate=True)
        z += hb
        if tier == 0:
            H = z
        u0, u1, v0, v1 = u0 + step, u1 - step, v0 + step, v1 - step
    top_z, top = z, (u0, u1, v0, v1)

    # Stair on the south face: 3DFACE treads and risers sharing vertices.
    W, R, n = U(10), U(6), 16
    d.truth.update(stair_w=W)
    vs = [-B / 2 - R + i * R / n for i in range(n + 1)]
    zs = [i * H / n for i in range(n + 1)]
    pts = {}

    def P(u, i, j):
        key = (u, i, j)
        if key not in pts:
            pts[key] = d.w(u, vs[i], zs[j])
        return pts[key]

    attribs = d.layer('ESCALINATA')
    for i in range(n):
        d.msp.add_3dface([P(-W / 2, i, i), P(W / 2, i, i), P(W / 2, i, i + 1), P(-W / 2, i, i + 1)], dxfattribs=attribs)
        d.msp.add_3dface([P(-W / 2, i, i + 1), P(W / 2, i, i + 1), P(W / 2, i + 1, i + 1), P(-W / 2, i + 1, i + 1)], dxfattribs=attribs)

    # Balustrades (alfardas) as polyface meshes with sloping tops.
    w = U(1)
    d.truth.update(alfarda_w=w)
    for ua, ub in ((W / 2, W / 2 + w), (-W / 2 - w, -W / 2)):
        vb, vt = -B / 2 - R, -B / 2
        c = [d.w(ua, vb, 0), d.w(ub, vb, 0), d.w(ub, vt, 0), d.w(ua, vt, 0),
             d.w(ua, vb, 0.4), d.w(ub, vb, 0.4), d.w(ub, vt, H + 0.4), d.w(ua, vt, H + 0.4)]
        pf = d.msp.add_polyface(dxfattribs=d.layer('ALFARDA'))
        pf.append_faces([[c[i] for i in q] for q in
                         ((0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))])

    # A summit temple drawn as a hypothesis on a 1 m module: must stay out of the test.
    tu, tv = (top[0] + top[1]) / 2, (top[2] + top[3]) / 2
    for (a0, a1, b0, b1) in ((-4.5, 4.5, -3.5, -2.5), (-4.5, 4.5, 2.5, 3.5), (-4.5, -3.5, -2.5, 2.5), (3.5, 4.5, -2.5, 2.5)):
        d.rect('TEMPLO SUPERIOR - RECONSTRUCCION HIPOTETICA', tu + a0, tu + a1, tv + b0, tv + b1, top_z, thickness=2.5)

    # Eroded wall stubs on the top platform.
    for k, vv in ((8, top[2] + 1.0), (6, top[2] + 3.0)):
        d.line('MURO EROSIONADO', (top[0] + 1, vv, top_z), (top[0] + 1 + U(k) - d.rng.uniform(0.1, 0.6), vv, top_z))

    # Things a real drawing carries that are not ancient design.
    solid = d.msp.add_3dsolid(dxfattribs=d.layer('ESCULTURA'))     # a real ACIS solid the reader cannot open
    cube = forms.cube().scale_uniform(d.scale).translate(d.w(0, -B / 2 - R - 2, 0.5, jit=False))
    acis.export_dxf(solid, [acis.body_from_mesh(cube)])
    for i in range(-5, 6):
        d.line('RETICULA', (i * 10.0, -60, 0), (i * 10.0, 60, 0))
        d.line('RETICULA', (-60, i * 10.0, 0), (60, i * 10.0, 0))
    for cu, cv in ((-30, -26), (-30, 26), (31, -24)):
        d.rect('SONDEO 2X2', cu, cu + 2.0, cv, cv + 2.0)
    for r, zc in ((34, -1.0), (42, -2.0), (50, -3.0)):
        ring = [d.w(r * (1 + 0.06 * math.sin(3 * t)) * math.cos(t), r * math.sin(t), zc, jit=False)
                for t in np.linspace(0, 2 * math.pi, 60, endpoint=False)]
        d.msp.add_lwpolyline([(p[0], p[1]) for p in ring], close=True,
                             dxfattribs=d.layer('CURVAS DE NIVEL') | {'elevation': ring[0][2]})
    d.msp.add_circle(d.w(0, -B / 2 - R - 4, 0), 0.6 * d.scale, dxfattribs=d.layer('OFRENDA'))
    d.msp.add_arc(d.w(3, -B / 2 - R - 4, 0), 0.9 * d.scale, 0, 180, dxfattribs=d.layer('OFRENDA'))
    d.msp.add_text('Estructura 1', dxfattribs=d.layer('TEXTO')).set_placement(d.w(0, -B / 2 - R - 8, 0)[:2])
    d.msp.add_linear_dim(base=d.w(0, -B / 2 - R - 6, 0)[:2], p1=d.w(-A / 2, -B / 2, 0)[:2], p2=d.w(A / 2, -B / 2, 0)[:2],
                         angle=-d.grid_deg, dxfattribs=d.layer('COTAS')).render()


def compound(d):
    """An apartment-compound fragment: rooms around a patio, a portico of pillars, a floor and a bench."""
    U = d.dim
    P, t, D, h = U(12), U(1), U(6), U(3)
    d.truth.update(patio_w=P, wall_t=t, room_d=D, wall_h=h)
    a, b = P / 2, P / 2 + t                       # inner ring
    c, e = b + D, b + D + t                       # outer ring
    walls = [(-b, b, a, b), (-b, b, -b, -a), (a, b, -a, a), (-b, -a, -a, a),
             (-e, e, c, e), (-e, e, -e, -c), (c, e, -c, c), (-e, -c, -c, c)]
    x = -b + U(5)
    walls += [(x, x + t, b, c), (-x - t, -x, b, c)]  # cross walls splitting the north rooms
    for wall in walls:
        d.rect('FASE 2 MUROS', *wall, thickness=h)
    for k, vv in ((10, -e - 4), (7, -e - 4 - U(1))):
        d.line('FASE 1 MUROS', (-e, vv, -1.2), (-e + U(k), vv, -1.2))

    # Pillars: a block drawn in metres, inserted at 100x scale into this drawing.
    pw, ph, s = U(1), U(3), U(4)
    d.truth.update(pillar_w=pw, pillar_gap=s - pw)
    blk = d.doc.blocks.new(name='PILAR')
    q = pw / 2
    corners = [(-q, -q), (q, -q), (q, q), (-q, q)]
    for i in range(4):
        (x0, y0), (x1, y1) = corners[i], corners[(i + 1) % 4]
        blk.add_line((x0, y0, 0), (x1, y1, 0))
        blk.add_line((x0, y0, ph), (x1, y1, ph))
        blk.add_line((x0, y0, 0), (x0, y0, ph))
    rot = -d.grid_deg
    k = d.scale
    for i in range(4):
        d.msp.add_blockref('PILAR', d.w((i - 1.5) * s, a - U(1.5), 0, jit=False),
                           dxfattribs=d.layer('PILARES') | {'rotation': rot, 'xscale': k, 'yscale': k, 'zscale': k})
    d.msp.add_blockref('PILAR', d.w(-s, -a + U(1.5), 0, jit=False),
                       dxfattribs=d.layer('PILARES') | {'rotation': rot, 'xscale': k, 'yscale': k, 'zscale': k,
                                                        'column_count': 3, 'column_spacing': s * k})

    # Patio floor as nine coplanar 3DFACE tiles sharing vertices.
    g = {(i, j): d.w(-a + i * P / 3, -a + j * P / 3, 0.0) for i in range(4) for j in range(4)}
    for i in range(3):
        for j in range(3):
            d.msp.add_3dface([g[i, j], g[i + 1, j], g[i + 1, j + 1], g[i, j + 1]], dxfattribs=d.layer('PISO ESTUCO'))

    # A bench as a closed 3D polyline, a talud skirt as a polygon mesh.
    bw, bl = U(2), U(10)
    d.msp.add_polyline3d([d.w(u, v, 0.45) for u, v in ((-a, -bl / 2), (-a + bw, -bl / 2), (-a + bw, bl / 2), (-a, bl / 2))],
                         close=True, dxfattribs=d.layer('BANQUETA'))
    pm = d.msp.add_polymesh(size=(2, 5), dxfattribs=d.layer('TALUD PATIO'))
    for j in range(5):
        u = -a + j * P / 4
        pm.set_mesh_vertex((0, j), d.w(u, -a + 0.6, 0.0))
        pm.set_mesh_vertex((1, j), d.w(u, -a, 0.6))

    # A drain with curved stretches (2D polyline with bulges) and a restored room.
    p0, p1, p2, p3 = d.w(e + 1, -4, 0), d.w(e + 1, 2, 0), d.w(e + 3, 4, 0), d.w(e + 3, 9, 0)
    d.msp.add_polyline2d([(p0[0], p0[1], 0), (p1[0], p1[1], 0.4), (p2[0], p2[1], 0), (p3[0], p3[1], 0)], format='xyb',
                         dxfattribs=d.layer('DRENAJE'))
    d.rect('CUARTO RESTAURADO', e + 2, e + 2 + U(5), -c, -c + U(4))
    d.msp.add_line(d.w(-e, e + 3, 0), d.w(-e + 1.7, e + 3.4, 0), dxfattribs={'layer': '0'})


def make_set(folder, unit=0.83, seed=1):
    """Write the two test drawings; returns {name: truth}."""
    out = {}
    for name, build, units, grid, s in (('estructura_1', temple, 'm', 15.5, seed), ('conjunto_2', compound, 'cm', 15.9, seed + 1)):
        d = Drafter(unit=unit, seed=s, units=units, grid_deg=grid)
        build(d)
        d.doc.saveas(f'{folder}/{name}.dxf')
        out[name] = d.truth | {'units': units, 'grid_deg': grid}
    return out
