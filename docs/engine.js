// Deterministic film engine. renderAt(t) draws the frame for time t (seconds)
// from story.js's TIMELINE, so the rendered MP4, the live preview and the app
// all agree on what happens when.
import * as THREE from 'three';
import { LineSegments2 } from 'three/addons/lines/LineSegments2.js';
import { LineSegmentsGeometry } from 'three/addons/lines/LineSegmentsGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { CHAPTERS, TIMELINE, TITLE, chapterAt, STATS, HYPOTHESES, EXAMPLE_RECORDS, SKEPTICS, ROLES, OUTCOMES, WEEKS } from './story.js';
import { synth, grid, scan, threshold, peak, valueAt } from './quanto.js';

const W = 1920, H = 1080;
const DEG = Math.PI / 180;
const ROT = -15.5 * DEG;                       // the city grid, 15.5° east of north
const CR = Math.cos(ROT), SR = Math.sin(ROT);

// City-local coordinates: x along the grid's east axis, z along its south axis.
const toWorld = (x, z) => [x * CR + z * SR, -x * SR + z * CR];
const toLocal = (wx, wz) => [wx * CR - wz * SR, wx * SR + wz * CR];

const clamp01 = (x) => Math.max(0, Math.min(1, x));
const smooth = (x) => { x = clamp01(x); return x * x * (3 - 2 * x); };
const easeIO = (x) => { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const lerp = (a, b, k) => a + (b - a) * k;
const ramp = (t, a, d = 0.6) => smooth((t - a) / d);
const win = (t, a, b, fi = 0.5, fo = 0.5) => Math.min(ramp(t, a, fi), 1 - ramp(t, b - fo, fo));

// Deterministic value noise for terrain.
function hash2(i, j) {
  let h = (i * 374761393 + j * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
function vnoise(x, z) {
  const i = Math.floor(x), j = Math.floor(z);
  const fx = x - i, fz = z - j;
  const u = fx * fx * (3 - 2 * fx), v = fz * fz * (3 - 2 * fz);
  const a = hash2(i, j), b = hash2(i + 1, j), c = hash2(i, j + 1), d = hash2(i + 1, j + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function fbm(x, z, oct = 5) {
  let s = 0, a = 0.5, f = 1;
  for (let o = 0; o < oct; o++) { s += a * (vnoise(x * f + o * 17.3, z * f - o * 9.1) * 2 - 1); f *= 2.03; a *= 0.5; }
  return s;
}
const g2 = (x, z) => Math.exp(-(x * x + z * z));

function heightAt(wx, wz) {
  const [lx, lz] = toLocal(wx, wz);
  const base = -0.004 * wz;
  let h = base;
  h += 560 * g2((wx + 900) / 2100, (wz + 5600) / 1700);   // Cerro Gordo, north
  h += 330 * g2((wx - 600) / 3200, (wz - 5800) / 1400);   // southern range
  h += 220 * g2((wx - 5900) / 1700, (wz + 500) / 2600);   // eastern hills
  h += 170 * g2((wx + 6200) / 1600, (wz - 1200) / 2400);  // western hills
  const hill = clamp01((h - base - 15) / 220);
  h += fbm(wx / 1100, wz / 1100) * (6 + 70 * hill) + fbm(wx / 170, wz / 170, 3) * (0.7 + 7 * hill);
  const core = smooth(1.15 - Math.max(Math.abs(lx) / 1900, Math.abs(lz - 250) / 1800));
  h = lerp(h, base + fbm(wx / 900, wz / 900, 3) * 1.5, core);
  const rz = 860 + 110 * Math.sin(lx / 420);                // San Juan river channel
  h -= 3.4 * Math.exp(-((lz - rz) ** 2) / (2 * 24 * 24));
  return h;
}

function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ---------------------------------------------------------------- geometry

function frustumGeo(w0, d0, w1, d1, h) {
  const p = [
    [-w0 / 2, 0, -d0 / 2], [w0 / 2, 0, -d0 / 2], [w0 / 2, 0, d0 / 2], [-w0 / 2, 0, d0 / 2],
    [-w1 / 2, h, -d1 / 2], [w1 / 2, h, -d1 / 2], [w1 / 2, h, d1 / 2], [-w1 / 2, h, d1 / 2],
  ];
  const quads = [[4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]];
  const pos = [];
  for (const [a, b, c, d] of quads) pos.push(...p[a], ...p[b], ...p[c], ...p[a], ...p[c], ...p[d]);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}

// A talud-tablero platform: sloped talud, vertical tablero panel, repeated per tier.
function buildTT(spec, mats, opts = {}) {
  const { w, d, tiers, talud = 0.62, slope = 0.6, overhang = 0.3, margin = 1.2 } = spec;
  const group = new THREE.Group();
  const parts = [];
  const dims = [];
  let y = 0, w0 = w, d0 = d;
  const tops = [];
  tiers.forEach((th, i) => {
    const talH = th * talud, tabH = th - talH;
    const w1 = w0 - 2 * talH * slope, d1 = d0 - 2 * talH * slope;
    const tal = new THREE.Mesh(frustumGeo(w0, d0, w1, d1, talH), mats.stone);
    tal.position.y = y;
    const tab = new THREE.Mesh(new THREE.BoxGeometry(w1 + 2 * overhang, tabH, d1 + 2 * overhang), mats.stone);
    tab.position.y = y + talH + tabH / 2;
    group.add(tal, tab);
    parts.push({ mesh: tal, layer: 'TALUD' }, { mesh: tab, layer: 'TABLERO' });
    tops.push({ y0: y, talH, tabH, w0, d0, w1, d1 });
    y += th;
    w0 = w1 - 2 * margin; d0 = d1 - 2 * margin;
  });
  const height = y;
  const topW = w0, topD = d0;

  if (spec.stairs) {
    // Stairs on the +x face, rising from the ground to the top platform.
    const { width, risers } = spec.stairs;
    const r = height / risers;
    const xBase = w / 2 + (spec.stairs.ext ?? height * 0.25);
    const run = xBase - topW / 2;
    const tread = run / risers;
    const steps = [];
    for (let k = 0; k < risers; k++) {
      const hgt = (k + 1) * r;
      const x0 = xBase - k * tread;
      const len = Math.max(tread, x0 - topW / 2 + 0.2);
      const step = new THREE.Mesh(new THREE.BoxGeometry(opts.coarseStairs ? len : tread + 0.02, hgt, width), mats.stair || mats.stone);
      step.position.set(x0 - (opts.coarseStairs ? len / 2 : tread / 2), hgt / 2, 0);
      if (!opts.coarseStairs || k % 3 === 0) { group.add(step); steps.push(step); parts.push({ mesh: step, layer: 'ESCALERA' }); }
    }
    const alfW = spec.stairs.alfarda || 0.83;
    const slopeLen = Math.hypot(run, height);
    const ang = Math.atan2(height, run);
    for (const side of [-1, 1]) {
      const alf = new THREE.Mesh(new THREE.BoxGeometry(slopeLen, 0.7, alfW), mats.stone);
      alf.position.set(xBase - run / 2, height / 2 + 0.2, side * (width / 2 + alfW / 2));
      alf.rotation.z = -ang;
      group.add(alf);
      parts.push({ mesh: alf, layer: 'ALFARDA' });
    }
    dims.push(
      { a: [xBase + 0.6, 0.05, -width / 2], b: [xBase + 0.6, 0.05, width / 2], label: `${width.toFixed(2)} m`, type: 'stair width', layer: 'ESCALERA', value: width },
      { a: [xBase - tread * 2, r * 2 + 0.05, width / 2 + alfW + 0.3], b: [xBase - tread * 3, r * 2 + 0.05, width / 2 + alfW + 0.3], label: `${tread.toFixed(2)} m`, type: 'stair tread', layer: 'ESCALERA', value: tread },
      { a: [xBase - tread * 5 + 0.05, r * 5, -width / 2 - alfW - 0.3], b: [xBase - tread * 5 + 0.05, r * 6, -width / 2 - alfW - 0.3], label: `${r.toFixed(2)} m`, type: 'stair riser', layer: 'ESCALERA', value: r },
      { a: [xBase - run * 0.35, height * 0.35 + 0.9, width / 2], b: [xBase - run * 0.35, height * 0.35 + 0.9, width / 2 + alfW], label: `${alfW.toFixed(2)} m`, type: 'alfarda width', layer: 'ALFARDA', value: alfW },
    );
  }

  if (spec.temple) {
    const { tw, td, th, wall } = spec.temple;
    const walls = [
      [tw, th, wall, 0, -td / 2 + wall / 2],
      [tw, th, wall, 0, td / 2 - wall / 2],
      [wall, th, td - 2 * wall, -tw / 2 + wall / 2, 0],
    ];
    for (const [a, b, c, x, z] of walls) {
      const m = new THREE.Mesh(new THREE.BoxGeometry(a, b, c), mats.stucco || mats.stone);
      m.position.set(x, height + b / 2, z);
      group.add(m);
      parts.push({ mesh: m, layer: 'MURO' });
    }
    dims.push(
      { a: [-tw / 2, height + th + 0.15, td / 2 + 0.6], b: [tw / 2, height + th + 0.15, td / 2 + 0.6], label: `${tw.toFixed(2)} m`, type: 'room width', layer: 'MURO', value: tw },
      { a: [tw / 2 + 0.3, height + th + 0.15, -td / 2], b: [tw / 2 + 0.3, height + th + 0.15, -td / 2 + wall], label: `${wall.toFixed(2)} m`, type: 'wall thickness', layer: 'MURO', value: wall },
    );
  }

  const t0 = tops[0], t1 = tops[1], tl = tops[tops.length - 1];
  dims.push(
    { a: [-w / 2, 0.1, d / 2 + 1.2], b: [w / 2, 0.1, d / 2 + 1.2], label: `${w.toFixed(2)} m`, type: 'platform side', layer: 'TALUD', value: w },
    { a: [-t0.w0 / 2 - 1.0, 0, d / 2 * 0.2], b: [-t0.w0 / 2 - 1.0, t0.talH, d / 2 * 0.2], label: `${t0.talH.toFixed(2)} m`, type: 'talud height', layer: 'TALUD', value: t0.talH },
    { a: [-t0.w1 / 2 - 1.2, t0.talH, -d / 2 * 0.3], b: [-t0.w1 / 2 - 1.2, t0.talH + t0.tabH, -d / 2 * 0.3], label: `${t0.tabH.toFixed(2)} m`, type: 'tablero height', layer: 'TABLERO', value: t0.tabH },
    { a: [-t1.w0 / 2, t1.y0 + 0.1, -t1.d0 / 2 - 0.8], b: [t1.w0 / 2, t1.y0 + 0.1, -t1.d0 / 2 - 0.8], label: `${t1.w0.toFixed(2)} m`, type: 'tier 2 side', layer: 'TALUD', value: t1.w0 },
    { a: [-tl.w1 / 2 - 0.9, tl.y0 + tl.talH, tl.d1 / 2 * 0.1], b: [-tl.w1 / 2 - 0.9, tl.y0 + tl.talH + tl.tabH, tl.d1 / 2 * 0.1], label: `${tl.tabH.toFixed(2)} m`, type: 'upper tablero', layer: 'TABLERO', value: tl.tabH },
    { a: [topW / 2 + 0.2, height + 0.1, -topD / 2], b: [topW / 2 + 0.2, height + 0.1, topD / 2], label: `${topD.toFixed(2)} m`, type: 'top platform', layer: 'PISO', value: topD },
  );

  group.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
  return { group, parts, dims, height, topW, topD };
}

function fatLines(positions, color, width = 2, opts = {}) {
  const g = new LineSegmentsGeometry();
  g.setPositions(positions);
  const m = new LineMaterial({ color, linewidth: width, transparent: true, opacity: 1, depthTest: opts.depthTest ?? true, dashed: !!opts.dashed, dashSize: opts.dashSize || 10, gapSize: opts.gapSize || 8 });
  m.resolution.set(W, H);
  const l = new LineSegments2(g, m);
  if (opts.dashed) l.computeLineDistances();
  l.renderOrder = opts.renderOrder || 2;
  return l;
}

function edgesOf(mesh, threshold = 25) {
  const e = new THREE.EdgesGeometry(mesh.geometry, threshold);
  mesh.updateMatrix();
  e.applyMatrix4(mesh.matrix);
  return Array.from(e.attributes.position.array);
}

// ---------------------------------------------------------------- the film

export async function createFilm(stage, opts = {}) {
  const glCanvas = document.createElement('canvas');
  glCanvas.width = W; glCanvas.height = H;
  stage.appendChild(glCanvas);
  const renderer = new THREE.WebGLRenderer({ canvas: glCanvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(opts.pixelRatio || 1);   // < 1 renders the 3D layer below 1080p on slower screens
  renderer.setSize(W, H, false);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const fx = document.createElement('canvas');
  fx.width = W; fx.height = H;
  stage.appendChild(fx);
  const ctx = fx.getContext('2d');

  const scene = new THREE.Scene();
  const fogNatural = new THREE.Color('#c9a888');
  const fogLidar = new THREE.Color('#0b1012');
  scene.fog = new THREE.FogExp2(fogNatural.clone(), 0.00011);
  const camera = new THREE.PerspectiveCamera(38, W / H, 2, 60000);

  // Sky dome
  const skyU = { uLidar: { value: 0 }, uSun: { value: new THREE.Vector3(0.74, 0.26, 0.5).normalize() } };
  const sky = new THREE.Mesh(new THREE.SphereGeometry(40000, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false, uniforms: skyU,
    vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `varying vec3 vDir; uniform float uLidar; uniform vec3 uSun;
      void main(){
        float y = clamp(vDir.y, -0.2, 1.0);
        vec3 zen = vec3(0.10,0.14,0.26), hor = vec3(0.98,0.66,0.42), low = vec3(0.55,0.40,0.36);
        vec3 c = mix(hor, zen, pow(clamp(y*1.8,0.0,1.0), 0.6));
        c = mix(low, c, smoothstep(-0.08, 0.04, y));
        float s = max(dot(normalize(vDir), uSun), 0.0);
        c += vec3(1.0,0.72,0.45) * (pow(s, 24.0) * 0.55 + pow(s, 400.0) * 2.0);
        vec3 dark = mix(vec3(0.03,0.045,0.05), vec3(0.06,0.08,0.09), clamp(y*2.0,0.0,1.0));
        gl_FragColor = vec4(mix(c, dark, uLidar), 1.0);
      }`,
  }));
  scene.add(sky);

  const sunDir = skyU.uSun.value.clone();
  const sun = new THREE.DirectionalLight(new THREE.Color('#ffd2a1'), 3.1);
  sun.castShadow = true;
  sun.shadow.mapSize.set(4096, 4096);
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.6;
  scene.add(sun, sun.target);
  const hemi = new THREE.HemisphereLight(new THREE.Color('#9fb6d6'), new THREE.Color('#6b5038'), 0.85);
  scene.add(hemi);

  // ------------------------------------------------------------ terrain
  const TER = 16000, SEG = 400;
  const tgeo = new THREE.PlaneGeometry(TER, TER, SEG, SEG);
  tgeo.rotateX(-Math.PI / 2);
  const tp = tgeo.attributes.position;
  for (let i = 0; i < tp.count; i++) tp.setY(i, heightAt(tp.getX(i), tp.getZ(i)));
  tgeo.computeVertexNormals();
  const terU = {
    uLidar: { value: 0 }, uModern: { value: 0 }, uRot: { value: new THREE.Vector2(CR, SR) },
  };
  const terMat = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.97, metalness: 0 });
  terMat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, terU);
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vWP; varying vec3 vWN;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvWP = (modelMatrix * vec4(transformed,1.0)).xyz; vWN = normalize(mat3(modelMatrix) * objectNormal);');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>
        varying vec3 vWP; varying vec3 vWN; uniform float uLidar; uniform float uModern; uniform vec2 uRot;
        float h21(vec2 p){ p = fract(p*vec2(123.34, 456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
        float vn(vec2 p){ vec2 i=floor(p), f=fract(p); vec2 u=f*f*(3.0-2.0*f);
          return mix(mix(h21(i),h21(i+vec2(1,0)),u.x), mix(h21(i+vec2(0,1)),h21(i+vec2(1,1)),u.x), u.y); }
        vec2 toLocal(vec2 w){ return vec2(w.x*uRot.x - w.y*uRot.y, w.x*uRot.y + w.y*uRot.x); }
        // Modern fields: plough lines in cells, most aligned with the old grid.
        float fieldPattern(vec2 L, out float edge){
          vec2 C = vec2(230.0, 170.0);
          vec2 cell = floor(L/C);
          float r = h21(cell);
          float ang = r < 0.68 ? (h21(cell+3.1) < 0.5 ? 0.0 : 1.5708) : (r-0.68)*9.0;
          vec2 dir = vec2(cos(ang), sin(ang));
          float s = dot(L, dir)/14.0;
          float fw = fwidth(s);
          // Band-limit the plough lines: gone well before they near the pixel grid.
          float stripe = 0.5 + 0.5*sin(6.2831*s);
          stripe = mix(stripe, 0.5, smoothstep(0.06, 0.25, fw));
          // Field boundaries, anti-aliased on their distance in metres.
          vec2 q = abs(fract(L/C) - 0.5) * C;
          float d = min(C.x*0.5 - q.x, C.y*0.5 - q.y);
          float aa = max(fwidth(d), 1e-3);
          float w = 1.2;
          edge = 1.0 - smoothstep(w - aa, w + aa, d);
          edge *= 1.0 - smoothstep(0.5*w, 2.0*w, aa);
          return stripe;
        }`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        {
          vec2 L = toLocal(vWP.xz);
          float slope = 1.0 - clamp(vWN.y, 0.0, 1.0);
          float n1 = vn(vWP.xz/180.0), n2 = vn(vWP.xz/34.0);
          vec3 soil = mix(vec3(0.42,0.33,0.24), vec3(0.55,0.44,0.31), n1);
          vec3 grass = mix(vec3(0.33,0.34,0.20), vec3(0.42,0.40,0.24), n2);
          vec3 rock = vec3(0.36,0.33,0.30);
          vec3 c = mix(soil, grass, smoothstep(0.35,0.7,n1*0.7+n2*0.3));
          c = mix(c, rock, smoothstep(0.08, 0.3, slope));
          float park = step(abs(L.x), 520.0) * step(-1320.0, L.y) * step(L.y, 1050.0);
          float edge;
          float f = fieldPattern(L, edge);
          float fieldsOn = uModern * (1.0 - park) * (1.0 - smoothstep(0.05, 0.16, slope));
          vec3 fc = mix(vec3(0.36,0.29,0.21), vec3(0.50,0.45,0.30), h21(floor(L/vec2(230.0,170.0))+7.0));
          c = mix(c, fc * (0.86 + 0.28*f), fieldsOn*0.85);
          c *= 1.0 - edge*fieldsOn*0.35;
          diffuseColor.rgb = c * c * 1.25;
        }`)
      .replace('#include <tonemapping_fragment>', `
        {
          vec3 n = normalize(vWN);
          float hs = 0.55*max(dot(n, normalize(vec3(-0.6,0.75,-0.4))),0.0) + 0.25*max(dot(n, normalize(vec3(0.5,0.7,-0.5))),0.0) + 0.2*max(dot(n, normalize(vec3(0.0,1.0,0.6))),0.0);
          vec2 L = toLocal(vWP.xz);
          float park = step(abs(L.x), 520.0) * step(-1320.0, L.y) * step(L.y, 1050.0);
          float edge;
          float f = fieldPattern(L, edge);
          float slope = 1.0 - clamp(n.y, 0.0, 1.0);
          float fieldsOn = uModern * (1.0 - park) * (1.0 - smoothstep(0.05, 0.16, slope));
          hs *= 1.0 + (f-0.5)*0.07*fieldsOn;
          float h = vWP.y;
          float cw = max(fwidth(h/10.0), 1e-4);
          float cl = abs(fract(h/10.0 - 0.5) - 0.5) / cw;
          float contour = (1.0 - clamp(cl, 0.0, 1.0)) * smoothstep(20.0, 60.0, h) * (1.0 - smoothstep(0.2, 0.5, cw));
          vec3 ramp = mix(vec3(0.10,0.17,0.18), vec3(0.62,0.62,0.58), clamp(h/450.0 + 0.25, 0.0, 1.0));
          vec3 lid = ramp * (0.25 + 1.35*hs);
          lid = mix(lid, vec3(0.75,0.58,0.40), edge*fieldsOn*0.35);
          lid += contour * 0.05;
          gl_FragColor.rgb = mix(gl_FragColor.rgb, lid * lid, uLidar);
        }
        #include <tonemapping_fragment>`);
  };
  const terrain = new THREE.Mesh(tgeo, terMat);
  terrain.receiveShadow = true;
  scene.add(terrain);

  // ------------------------------------------------------------ the city
  const mats = {
    stone: new THREE.MeshStandardMaterial({ color: new THREE.Color('#9a8a74'), roughness: 0.93, flatShading: true }),
    stucco: new THREE.MeshStandardMaterial({ color: new THREE.Color('#b9ab94'), roughness: 0.9, flatShading: true }),
    plaza: new THREE.MeshStandardMaterial({ color: new THREE.Color('#a8997f'), roughness: 0.95 }),
    street: new THREE.MeshStandardMaterial({ color: new THREE.Color('#8f7f69'), roughness: 0.96 }),
  };
  const stoneBase = mats.stone.color.clone();
  const city = new THREE.Group();
  city.rotation.y = ROT;
  scene.add(city);
  const groundAt = (x, z) => { const [wx, wz] = toWorld(x, z); return heightAt(wx, wz); };
  const place = (obj, x, z, yOff = 0) => { obj.position.set(x, groundAt(x, z) + yOff, z); city.add(obj); return obj; };

  const MOON = [0, -1180], SUN = [190, -420], CIUD = [270, 700], GREAT = [-250, 700];
  const HERO = [-150, -760];

  const sunPyr = buildTT({ w: 222, d: 222, tiers: [16, 15, 14, 12, 8], talud: 0.7, slope: 1.7, overhang: 1.4, margin: 3, stairs: { width: 22, risers: 34, alfarda: 3 } }, mats, { coarseStairs: true });
  sunPyr.group.rotation.y = Math.PI;      // stairs face west, toward the street
  place(sunPyr.group, SUN[0], SUN[1], -1);
  const moonPyr = buildTT({ w: 146, d: 140, tiers: [12, 11, 10, 7, 3], talud: 0.7, slope: 1.45, overhang: 1.2, margin: 3, stairs: { width: 18, risers: 30, alfarda: 2.6 } }, mats, { coarseStairs: true });
  moonPyr.group.rotation.y = -Math.PI / 2; // stairs face south
  place(moonPyr.group, MOON[0], MOON[1], -1);

  // Plazas
  const plaza = (x, z, w, d, h = 0.8) => place(new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mats.plaza), x, z, h / 2 - 0.5);
  plaza(MOON[0], MOON[1] + 140, 200, 150);
  plaza(SUN[0], SUN[1], 360, 330);
  plaza(HERO[0], HERO[1], 92, 92, 0.9);

  // Street of the Dead with cross walls and flanking platforms
  const street = new THREE.Mesh(new THREE.BoxGeometry(44, 0.8, 2350), mats.street);
  place(street, 0, 70, -0.1);
  const r1 = rng(11);
  for (let z = -120; z < 560; z += 92) {
    const wall = new THREE.Mesh(new THREE.BoxGeometry(44, 3.2, 4), mats.stone);
    place(wall, 0, z, 1.2); wall.castShadow = true;
  }
  // Small talud-tablero temples flanking the street
  for (let z = -980; z < 1250; z += 70) {
    for (const side of [-1, 1]) {
      if (side > 0 && z > -620 && z < -220) continue;
      if (z > 470 && z < 940) continue;
      if (side < 0 && z > -820 && z < -700) continue;
      if (r1() < 0.32) continue;
      const s = 16 + r1() * 16;
      const t = buildTT({ w: s, d: s * (0.9 + r1() * 0.3), tiers: [3 + r1() * 2.5, 2.2 + r1() * 2], talud: 0.55, slope: 0.6, overhang: 0.3, margin: 1.2 }, mats);
      place(t.group, side * (40 + s / 2 + r1() * 18), z + r1() * 12, -0.2);
    }
  }
  // La Ciudadela: raised banks around a sunken plaza, small pyramids on top, and the Feathered Serpent Pyramid
  const bank = (x, z, w, d, h) => { const m = new THREE.Mesh(frustumGeo(w, d, w - 14, d - 14, h), mats.stone); m.castShadow = m.receiveShadow = true; return place(m, x, z, -0.4); };
  bank(CIUD[0], CIUD[1] - 190, 400, 46, 7); bank(CIUD[0], CIUD[1] + 190, 400, 46, 7);
  bank(CIUD[0] - 190, CIUD[1], 46, 340, 7); bank(CIUD[0] + 190, CIUD[1], 46, 340, 7);
  for (let i = 0; i < 4; i++) {
    for (const [dx, dz] of [[-150 + i * 100, -190], [-150 + i * 100, 190], [-190, -120 + i * 80]]) {
      const p = buildTT({ w: 18, d: 18, tiers: [4, 3.5], talud: 0.55, slope: 0.5, margin: 1 }, mats);
      place(p.group, CIUD[0] + dx, CIUD[1] + dz, 6.4);
    }
  }
  const fsp = buildTT({ w: 65, d: 65, tiers: [3.4, 3.3, 3.3, 3.2, 3.1, 3.0], talud: 0.36, slope: 0.6, overhang: 0.5, margin: 1.3 }, mats);
  place(fsp.group, CIUD[0] + 70, CIUD[1], -0.4);
  bank(GREAT[0], GREAT[1] - 140, 330, 70, 4.5); bank(GREAT[0], GREAT[1] + 140, 330, 70, 4.5);

  // The hero platform: the structure the film measures.
  const heroSpec = { w: 22, d: 22, tiers: [2.6, 2.4, 2.3, 2.1], talud: 0.62, slope: 0.45, overhang: 0.25, margin: 0.8, stairs: { width: 5.81, risers: 28, alfarda: 0.83, ext: 2.5 }, temple: { tw: 6.0, td: 5.2, th: 2.3, wall: 0.78 } };
  const heroMats = {
    stone: new THREE.MeshStandardMaterial({ color: new THREE.Color('#a39279'), roughness: 0.9, flatShading: true, transparent: true }),
    stucco: new THREE.MeshStandardMaterial({ color: new THREE.Color('#c2b39a'), roughness: 0.88, flatShading: true, transparent: true }),
  };
  heroMats.stair = heroMats.stone;
  const hero = buildTT(heroSpec, heroMats);
  place(hero.group, HERO[0], HERO[1], 0.4);
  const heroBase = hero.group.position.clone();

  // CAD wireframe of the hero, coloured by layer, in its own "drawing" frame.
  const LAYER_COL = { TALUD: '#3fbfa0', TABLERO: '#e0703f', ESCALERA: '#ece6d6', ALFARDA: '#c9b48a', MURO: '#86a9c8', PISO: '#8e958f' };
  const wire = new THREE.Group();
  const wireByLayer = {};
  for (const { mesh, layer } of hero.parts) (wireByLayer[layer] ||= []).push(...edgesOf(mesh));
  const wireLines = Object.entries(wireByLayer).map(([layer, pos]) => { const l = fatLines(pos, LAYER_COL[layer], 1.8); wire.add(l); return l; });
  const cadGrid = [];
  for (let i = -30; i <= 30; i += 2) cadGrid.push(i, 0.06, -30, i, 0.06, 30, -30, 0.06, i, 30, 0.06, i);
  const gridLines = fatLines(cadGrid, '#86a9c8', 1, { renderOrder: 1 });
  wire.add(gridLines);
  city.add(wire);
  wire.position.copy(heroBase);
  // City-grid reference lines (aligned with the 15.5° orientation)
  const refPos = [];
  for (let i = -40; i <= 40; i += 10) refPos.push(i, 0.1, -40, i, 0.1, 40, -40, 0.1, i, 40, 0.1, i);
  const refGrid = fatLines(refPos, '#3fbfa0', 1.4, { renderOrder: 1 });
  refGrid.position.copy(heroBase);
  city.add(refGrid);

  // Dimension lines on the hero
  const dimObjs = hero.dims.map((dm) => {
    const a = new THREE.Vector3(...dm.a), b = new THREE.Vector3(...dm.b);
    const dir = b.clone().sub(a).normalize();
    const up = Math.abs(dir.y) > 0.9 ? new THREE.Vector3(1, 0, 0) : new THREE.Vector3(0, 1, 0);
    const tick = new THREE.Vector3().crossVectors(dir, up).normalize().multiplyScalar(0.35);
    if (tick.lengthSq() < 1e-6) tick.set(0, 0.35, 0);
    const pos = [...a.toArray(), ...b.toArray(),
      ...a.clone().add(tick).toArray(), ...a.clone().sub(tick).toArray(),
      ...b.clone().add(tick).toArray(), ...b.clone().sub(tick).toArray()];
    const line = fatLines(pos, '#ece6d6', 2.4, { depthTest: false, renderOrder: 5 });
    line.position.copy(heroBase);
    city.add(line);
    const el = document.createElement('div');
    el.className = 'label3d';
    el.textContent = dm.label;
    return { ...dm, line, el, mid: a.clone().add(b).multiplyScalar(0.5) };
  });

  // Apartment compounds: thousands of walls on the grid
  const compounds = [];
  const r2 = rng(5);
  const near = (x, z, box) => x > box[0] && x < box[1] && z > box[2] && z < box[3];
  const exclude = [[-120, 120, -1320, -960], [-10, 400, -640, -200], [-210, -90, -820, -700], [50, 480, 470, 930], [-430, -60, 520, 880], [-60, 60, -1150, 1260]];
  for (let gx = -1750; gx <= 1750; gx += 64) {
    for (let gz = -1350; gz <= 1950; gz += 64) {
      const x = gx + (r2() - 0.5) * 8, z = gz + (r2() - 0.5) * 8;
      const dist = Math.hypot(x / 1800, (z - 250) / 1750);
      if (dist > 1 || r2() < 0.25 + 0.55 * dist * dist) continue;
      if (exclude.some((b) => near(x, z, b))) continue;
      const s = 38 + r2() * 22;
      compounds.push({ x, z, s, h: 2.6 + r2() * 1.6 });
    }
  }
  const wallGeo = new THREE.BoxGeometry(1, 1, 1);
  wallGeo.translate(0, 0.5, 0);
  const cMat = new THREE.MeshStandardMaterial({ color: stoneBase.clone(), roughness: 0.92, flatShading: true });
  const cMesh = new THREE.InstancedMesh(wallGeo, cMat, compounds.length * 6);
  const m4 = new THREE.Matrix4(), q4 = new THREE.Quaternion(), s3 = new THREE.Vector3(), p3 = new THREE.Vector3();
  let ci = 0;
  for (const c of compounds) {
    const y = groundAt(c.x, c.z) - 0.3;
    const t = 1.6;
    const ws = [[c.x, c.z - c.s / 2, c.s, t], [c.x, c.z + c.s / 2, c.s, t], [c.x - c.s / 2, c.z, t, c.s], [c.x + c.s / 2, c.z, t, c.s],
      [c.x - c.s * 0.12, c.z, t * 0.7, c.s * 0.5], [c.x, c.z + c.s * 0.18, c.s * 0.5, t * 0.7]];
    for (const [x, z, sx, sz] of ws) {
      m4.compose(p3.set(x, y, z), q4.identity(), s3.set(sx, c.h, sz));
      cMesh.setMatrixAt(ci++, m4);
    }
  }
  cMesh.count = ci;
  cMesh.castShadow = true; cMesh.receiveShadow = true;
  city.add(cMesh);

  // Modern layer: houses, roads
  const houses = [];
  const r3 = rng(21);
  const towns = [[-1000, 1650, 760, 1500], [1100, -1450, 560, 700], [2300, 1200, 520, 500], [-2300, -500, 480, 420], [700, 2300, 600, 520]];
  for (const [tx, tz, rad, n] of towns) {
    for (let i = 0; i < n; i++) {
      const a = r3() * Math.PI * 2, d = Math.sqrt(r3()) * rad;
      houses.push({ x: tx + Math.cos(a) * d, z: tz + Math.sin(a) * d });
    }
  }
  for (let i = 0; i < 900; i++) houses.push({ x: (r3() - 0.5) * 7000, z: (r3() - 0.5) * 7000 });
  const parkBox = [-520, 520, -1320, 1050];
  const hList = houses.filter((h) => !near(h.x, h.z, parkBox));
  const hMat = new THREE.MeshStandardMaterial({ color: new THREE.Color('#c6b296'), roughness: 0.85, flatShading: true });
  const hMesh = new THREE.InstancedMesh(wallGeo, hMat, hList.length);
  hList.forEach((h, i) => {
    const aligned = r3() < 0.62;
    const ang = aligned ? (r3() < 0.5 ? 0 : Math.PI / 2) : (r3() - 0.5) * 1.2;
    q4.setFromAxisAngle(new THREE.Vector3(0, 1, 0), ang);
    m4.compose(p3.set(h.x, groundAt(h.x, h.z) - 0.4, h.z), q4, s3.set(7 + r3() * 9, 3 + r3() * 4, 7 + r3() * 9));
    hMesh.setMatrixAt(i, m4);
  });
  hMesh.castShadow = true; hMesh.receiveShadow = true;
  const modern = new THREE.Group();
  modern.add(hMesh);
  city.add(modern);

  const roadMat = new THREE.MeshStandardMaterial({ color: new THREE.Color('#3a3631'), roughness: 0.8, transparent: true, opacity: 0 });
  const ribbon = (pts, width) => {
    const pos = [];
    const samples = [];
    for (let i = 0; i < pts.length - 1; i++) {
      const [x0, z0] = pts[i], [x1, z1] = pts[i + 1];
      const n = Math.max(2, Math.ceil(Math.hypot(x1 - x0, z1 - z0) / 25));
      for (let k = 0; k < n; k++) samples.push([lerp(x0, x1, k / n), lerp(z0, z1, k / n)]);
    }
    samples.push(pts[pts.length - 1]);
    for (let i = 0; i < samples.length - 1; i++) {
      const [x0, z0] = samples[i], [x1, z1] = samples[i + 1];
      const dx = x1 - x0, dz = z1 - z0, l = Math.hypot(dx, dz) || 1;
      const nx = -dz / l * width / 2, nz = dx / l * width / 2;
      const y0 = groundAt(x0, z0) + 0.5, y1 = groundAt(x1, z1) + 0.5;
      pos.push(x0 - nx, y0, z0 - nz, x1 - nx, y1, z1 - nz, x1 + nx, y1, z1 + nz, x0 - nx, y0, z0 - nz, x1 + nx, y1, z1 + nz, x0 + nx, y0, z0 + nz);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.computeVertexNormals();
    const m = new THREE.Mesh(g, roadMat);
    m.receiveShadow = true;
    modern.add(m);
  };
  ribbon([[-5200, 2600], [-2600, 2250], [-900, 2380], [700, 2050], [2300, 1100], [3400, 300], [5200, -200]], 16);
  ribbon([[-700, -1700], [-650, 300], [-700, 1300], [-500, 2300], [-100, 3600]], 10);
  ribbon([[700, -1900], [760, -400], [720, 1300], [900, 2600]], 10);
  ribbon([[-2600, -1600], [-1200, -1520], [400, -1650], [2300, -1500], [3600, -900]], 12);
  for (let x = -1500; x <= -500; x += 200) ribbon([[x, 1150], [x, 2150]], 7);
  for (let z = 1250; z <= 2150; z += 180) ribbon([[-1600, z], [-420, z]], 7);

  // 15.5° overlay at the Moon plaza
  const P = new THREE.Vector3(...[MOON[0], 0, MOON[1] + 260]);
  const pw = toWorld(P.x, P.z);
  const py = heightAt(pw[0], pw[1]) + 3;
  const north = fatLines([pw[0], py, pw[1], pw[0], py, pw[1] - 1500], '#ece6d6', 2.2, { dashed: true, dashSize: 14, gapSize: 10, depthTest: false });
  const ax = [Math.sin(15.5 * DEG), -Math.cos(15.5 * DEG)];
  const axis = fatLines([pw[0], py, pw[1], pw[0] + ax[0] * 1500, py, pw[1] + ax[1] * 1500], '#3fbfa0', 3.2, { depthTest: false });
  const arc = [];
  for (let i = 0; i < 24; i++) {
    const a0 = (i / 24) * 15.5 * DEG, a1 = ((i + 1) / 24) * 15.5 * DEG;
    arc.push(pw[0] + Math.sin(a0) * 900, py, pw[1] - Math.cos(a0) * 900, pw[0] + Math.sin(a1) * 900, py, pw[1] - Math.cos(a1) * 900);
  }
  const arcLine = fatLines(arc, '#e0703f', 3, { depthTest: false });
  scene.add(north, axis, arcLine);
  const arcLabelPos = new THREE.Vector3(pw[0] + Math.sin(7.7 * DEG) * 1000, py, pw[1] - Math.cos(7.7 * DEG) * 1000);

  // City-scale transect for Step 7
  const tz = -300;
  const crossings = [];
  for (const c of compounds) {
    if (Math.abs(c.z - tz) < c.s / 2 && c.x > -1500 && c.x < -150) crossings.push(c.x - c.s / 2, c.x + c.s / 2);
  }
  crossings.sort((a, b) => a - b);
  const tr = [];
  const ty = (x) => groundAt(x, tz) + 9;
  tr.push(-1500, ty(-1500), tz, -150, ty(-150), tz);
  for (const x of crossings) tr.push(x, ty(x) - 7, tz, x, ty(x) + 12, tz);
  const transect = fatLines(tr, '#e0703f', 2.6, { depthTest: false });
  city.add(transect);

  // Step 7: the vault's tile grid, draped on the terrain (1 km tiles, world axes)
  const tilePos = [];
  const drape = (x0, z0, x1, z1) => {
    const n = 20;
    for (let i = 0; i < n; i++) {
      const xa = lerp(x0, x1, i / n), za = lerp(z0, z1, i / n), xb = lerp(x0, x1, (i + 1) / n), zb = lerp(z0, z1, (i + 1) / n);
      tilePos.push(xa, heightAt(xa, za) + 6, za, xb, heightAt(xb, zb) + 6, zb);
    }
  };
  for (let x = -3000; x <= 3000; x += 1000) drape(x, -3000, x, 3000);
  for (let z = -3000; z <= 3000; z += 1000) drape(-3000, z, 3000, z);
  const tiles = fatLines(tilePos, '#86a9c8', 1.6, { depthTest: false });
  scene.add(tiles);
  const r5 = rng(77);
  const hex = (n) => Array.from({ length: n }, () => '0123456789abcdef'[Math.floor(r5() * 16)]).join('');
  const tileTags = [];
  for (let tx = -2; tx <= 1; tx++) for (let tz = -2; tz <= 1; tz++) {
    const cx = tx * 1000 + 500, cz = tz * 1000 + 500;
    tileTags.push({ pos: new THREE.Vector3(cx, heightAt(cx, cz) + 10, cz), text: `E${512 + tx}·N${2178 - tz}<br>${hex(4)}…${hex(4)}` });
  }

  // Step 8: a point cloud (ground, roofs, trees), then detected lines
  const ptsGround = [], colGround = [], ptsAbove = [], colAbove = [];
  const r6 = rng(91);
  const ramp4 = [new THREE.Color('#2b4c7e'), new THREE.Color('#2f9e8f'), new THREE.Color('#e9d16a'), new THREE.Color('#e0703f')];
  const elevColor = (t) => { t = clamp01(t) * 3; const i = Math.min(2, Math.floor(t)); return ramp4[i].clone().lerp(ramp4[i + 1], t - i); };
  for (let i = 0; i < 110000; i++) {
    const [wx, wz] = toWorld(-2100 + r6() * 3000, -1500 + r6() * 2600);
    const y = heightAt(wx, wz);
    ptsGround.push(wx, y + 0.3, wz);
    const c = elevColor((y + 0.004 * wz + 6) / 22 + (r6() - 0.5) * 0.08);
    colGround.push(c.r, c.g, c.b);
  }
  for (const c of compounds) {
    if (c.x < -2100 || c.x > 900 || c.z < -1500 || c.z > 1100) continue;
    for (let i = 0; i < 26; i++) {
      const side = i % 4, f = r6() - 0.5;
      const lx = side < 2 ? c.x + f * c.s : c.x + (side === 2 ? -0.5 : 0.5) * c.s;
      const lz = side < 2 ? c.z + (side === 0 ? -0.5 : 0.5) * c.s : c.z + f * c.s;
      const [wx, wz] = toWorld(lx, lz);
      ptsGround.push(wx, groundAt(lx, lz) + c.h * 0.8, wz);
      const col = elevColor(0.55 + (r6() - 0.5) * 0.1);
      colGround.push(col.r, col.g, col.b);
    }
  }
  const roof = new THREE.Color('#f2efe6'), leaf = new THREE.Color('#7fbf5f');
  for (const hh of hList) {
    if (hh.x < -2100 || hh.x > 900 || hh.z < -1500 || hh.z > 1100) continue;
    const top = groundAt(hh.x, hh.z) + 3 + r6() * 4;
    for (let i = 0; i < 14; i++) {
      const lx = hh.x + (r6() - 0.5) * 11, lz = hh.z + (r6() - 0.5) * 11;
      const [wx, wz] = toWorld(lx, lz);
      ptsAbove.push(wx, top, wz); colAbove.push(roof.r, roof.g, roof.b);
    }
  }
  for (let t = 0; t < 900; t++) {
    const lx = -2100 + r6() * 3000, lz = -1500 + r6() * 2600;
    const base = groundAt(lx, lz), hgt = 5 + r6() * 8;
    for (let i = 0; i < 12; i++) {
      const a = r6() * 6.283, rr = r6() * 4;
      const [wx, wz] = toWorld(lx + Math.cos(a) * rr, lz + Math.sin(a) * rr);
      ptsAbove.push(wx, base + hgt * (0.6 + r6() * 0.4), wz); colAbove.push(leaf.r, leaf.g, leaf.b);
    }
  }
  const mkPoints = (pos, col, size) => {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3));
    const m = new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true, transparent: true, depthWrite: false, fog: false });
    const p = new THREE.Points(g, m); p.renderOrder = 3; p.frustumCulled = false;
    return p;
  };
  const cloudGround = mkPoints(ptsGround, colGround, 2.0);
  const cloudAbove = mkPoints(ptsAbove, colAbove, 2.4);
  scene.add(cloudGround, cloudAbove);

  const ancientSeg = [], modernSeg = [];
  for (const c of compounds) {
    if (c.x < -2100 || c.x > 900 || c.z < -1500 || c.z > 1100) continue;
    const y = groundAt(c.x, c.z) + c.h + 1.5, h2 = c.s / 2;
    ancientSeg.push(c.x - h2, y, c.z - h2, c.x + h2, y, c.z - h2, c.x - h2, y, c.z + h2, c.x + h2, y, c.z + h2,
      c.x - h2, y, c.z - h2, c.x - h2, y, c.z + h2, c.x + h2, y, c.z - h2, c.x + h2, y, c.z + h2);
  }
  const modAngles = [];
  for (let i = 0; i < 520; i++) {
    const lx = -2100 + r6() * 3000, lz = -1500 + r6() * 2600;
    const aligned = r6() < 0.5;
    const a = aligned ? (r6() < 0.5 ? 0 : Math.PI / 2) + (r6() - 0.5) * 0.06 : r6() * Math.PI;
    const len = 30 + r6() * 70, y = groundAt(lx, lz) + 4;
    modernSeg.push(lx - Math.cos(a) * len / 2, y, lz - Math.sin(a) * len / 2, lx + Math.cos(a) * len / 2, y, lz + Math.sin(a) * len / 2);
    modAngles.push(((15.5 + a / DEG) % 180 + 180) % 180);
  }
  const linesAncient = fatLines(ancientSeg, '#ece6d6', 1.8, { depthTest: false, renderOrder: 4 });
  const linesModern = fatLines(modernSeg, '#ece6d6', 1.8, { depthTest: false, renderOrder: 4 });
  city.add(linesAncient, linesModern);
  // Orientation histogram of detected lines (degrees east of north, 0-180)
  const hist = new Float64Array(90);
  const nAnc = ancientSeg.length / 6;
  for (let i = 0; i < nAnc; i++) { const a = (i % 2 ? 105.5 : 15.5) + (r6() - 0.5) * 2.5; hist[Math.floor(((a % 180) + 180) % 180 / 2)]++; }
  for (const a of modAngles) hist[Math.floor(a / 2) % 90]++;
  const histMax = Math.max(...hist);

  // ------------------------------------------------------------ overlays
  const el = (html, cls = '', parent = stage) => { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; parent.appendChild(d); return d; };
  const labels = el('', 'layer');
  for (const d of dimObjs) labels.appendChild(d.el);
  const arcLabel = el('15.5°', 'label3d accent', labels);
  arcLabel.style.fontSize = '30px';
  const northLabel = el('true north', 'label3d', labels);
  const axisLabel = el('city axis', 'label3d', labels);
  const transectLabel = el('spacing between parallel walls', 'label3d', labels);

  const vignette = el('', 'layer'); vignette.id = 'vignette';
  const grain = el('', 'layer'); grain.id = 'grain';
  {
    const g = document.createElement('canvas'); g.width = g.height = 256;
    const gx = g.getContext('2d'); const img = gx.createImageData(256, 256); const rr = rng(3);
    for (let i = 0; i < img.data.length; i += 4) { const v = rr() * 255; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = 255; }
    gx.putImageData(img, 0, 0);
    grain.style.backgroundImage = `url(${g.toDataURL()})`;
  }
  const ui = el('', 'layer');
  const kicker = el('', 'o', ui); kicker.id = 'kicker';
  const ctitle = el('', 'o', ui); ctitle.id = 'ctitle';
  const note = el('', 'o', ui); note.id = 'note';

  const stats = STATS.map(([b, s], i) => { const d = el(`<b>${b}</b><span>${s}</span>`, 'o stat', ui); d.style.top = `${300 + i * 168}px`; return d; });
  const legend = el('<span><i style="background:var(--jade)"></i>ancient</span><span><i style="background:#c6b296"></i>modern</span>', 'o legend', ui);
  Object.assign(legend.style, { left: '96px', top: '820px' });

  const hyps = HYPOTHESES.map(({ label: b, note: s }, i) => { const d = el(`<b>${b}</b><span class="q">?</span><em>${s}</em>`, 'o hyp', ui); d.style.top = `${226 + i * 118}px`; return d; });

  const pipeline = el('DWG<span class="arrow">→</span>DXF<span class="arrow">→</span>geometry<span class="arrow">→</span>city-grid coordinates', 'o chip', ui);
  Object.assign(pipeline.style, { left: '96px', top: '214px' });
  const layersBox = el(Object.entries(LAYER_COL).map(([k, c]) => `<div><i style="background:${c}"></i>${k}</div>`).join(''), 'o layers', ui);
  const compass = el(`<svg class="compass" viewBox="-75 -75 150 150"><circle r="66" fill="rgba(8,10,11,0.6)" stroke="rgba(236,230,214,0.35)"/><line x1="0" y1="52" x2="0" y2="-52" stroke="#ece6d6" stroke-dasharray="6 5" stroke-width="2"/><g transform="rotate(15.5)"><line x1="0" y1="52" x2="0" y2="-56" stroke="#3fbfa0" stroke-width="4"/><path d="M0 -62 L-7 -48 L7 -48 Z" fill="#3fbfa0"/></g><text x="-4" y="-56" fill="#ece6d6" font-family="JetBrains Mono" font-size="15" text-anchor="end">N</text></svg>`, 'o', ui);
  Object.assign(compass.style, { left: '96px', top: '300px' });

  const records = el('<div class="head"><span>measurements.csv</span><span>source · layer · handle</span></div>', 'o', ui);
  records.id = 'records';
  const TAGCLS = { original: 'orig', restored: 'rest', eroded: 'eros' };
  const rows = dimObjs.map((d, i) => {
    const rec = EXAMPLE_RECORDS[i];
    d.el.textContent = `${rec.value.toFixed(2)} m`;
    const r = el(`<span class="id">${rec.id}</span><span>${rec.type}<br><small style="color:var(--mute);font-size:14px">${rec.layer} · h:${rec.handle}</small></span><span class="v">${rec.value.toFixed(2)} m</span><span class="tags"><span class="tag ${TAGCLS[rec.tag]}">${rec.tag.toUpperCase()}</span><span class="tag ph">PH ${rec.phase}</span></span>`, 'row', records);
    r.style.height = '50px';
    return r;
  });
  const blind = el('BLIND MODE · VALUES HIDDEN', 'o', ui); blind.id = 'blind';
  const stamp = el('FROZEN<small>pre-registered · osf · sha256 9f2c…e41a</small>', 'o', ui); stamp.id = 'stamp';


  const skWrap = el('', 'o', ui); skWrap.id = 'skeptics'; skWrap.style.opacity = 1;
  const sks = SKEPTICS.map(([q, a]) => el(`<div class="face front"><em>Objection</em><b>${q}</b></div><div class="face back"><em>Check that runs</em><b>${a}</b></div>`, 'sk', skWrap));

  const roles = el('', 'cards', ui); roles.style.gridTemplateColumns = 'repeat(3, 1fr)';
  const ROLE = ROLES.map(([e, b, p]) => el(`<em>${e}</em><b>${b}</b><p>${p}</p>`, 'card', roles));
  const lock = el('<i>■</i> Raw drawings and lidar stay in the lab. Claude sees lengths and tags, never coordinates.', 'o', ui); lock.id = 'lock';

  const tileLabels = tileTags.map((tt) => { const d = el(tt.text, 'label3d', labels); d.style.fontSize = '15px'; d.style.textAlign = 'center'; return d; });
  const vault = el(`<div class="head"><span>vault / manifest.json</span><span>size · sha256</span></div>` +
    [['tile_E511_N2179.copc.laz', '1.3 GB'], ['tile_E512_N2179.copc.laz', '1.1 GB'], ['tile_E511_N2178.copc.laz', '1.4 GB'], ['tile_E512_N2178.copc.laz', '1.2 GB'], ['dem_050cm.cog.tif', '6.2 GB'], ['relief_050cm.cog.tif', '6.2 GB'], ['params.yaml', '4 KB']]
      .map(([f, z]) => `<div class="row" style="grid-template-columns:1fr 92px 170px"><span>${f}</span><span class="v">${z}</span><span class="id">${hex(6)}…${hex(4)}</span></div>`).join(''), 'o', ui);
  vault.id = 'records';
  vault.style.width = '760px';
  const vaultRows = [...vault.querySelectorAll('.row')];
  const lockBadge = el('ENCRYPTED · LAB ONLY · NO PUBLIC CLOUD', 'o', ui); lockBadge.id = 'blind'; lockBadge.style.top = '150px';
  const flow = el(`<div class="flowrow"><b>Point clouds</b><em>COPC</em><span class="stay">stays</span></div><div class="flowrow"><b>Surfaces</b><em>COG</em><span class="stay">stays</span></div><div class="flowrow"><b>Lengths, angles, stats</b><em>CSV</em><span class="leave">leaves</span></div>`, 'o flow', ui);
  const stages = el(['points', 'ground', 'surface', 'relief', 'lines', 'classes'].map((x) => `<span>${x}</span>`).join('<i>→</i>'), 'o stages', ui);
  const stageEls = [...stages.querySelectorAll('span')];
  const lineLegend = el('<span><i style="background:var(--jade)"></i>ancient</span><span><i style="background:#c29a6b"></i>modern</span><span style="color:var(--mute)">trained on the 2021 mapped features · tested on ground-verified areas</span>', 'o legend', ui);
  Object.assign(lineLegend.style, { left: '96px', top: '290px', padding: '12px 16px', background: 'rgba(8,10,11,0.72)', borderRadius: '6px' });
  const jobs = el('', 'cards', ui); jobs.style.gridTemplateColumns = 'repeat(4, 1fr)'; jobs.style.top = '300px';
  const JOB = [['Builds', 'Pipeline and tests', 'CAD reader, lidar pipeline, classifier and statistics, each tested on data with a known answer.'],
    ['Reads', 'Messy to tidy', 'Spanish layer names, field notes and old reports become structured, citable rows.'],
    ['Remembers', 'A second dataset', 'Every published dimension of the city, with its source, for an independent check.'],
    ['Doubts', 'Skeptic agents', 'Attack each result and turn every objection into a check that runs.']]
    .map(([e, b, p]) => el(`<em>${e}</em><b>${b}</b><p>${p}</p>`, 'card', jobs));
  const guardBox = el(`<div class="gflow"><div class="gnode">Claude<br><small>proposes</small></div><i>→</i><div class="gnode accent">Archaeologist<br><small>confirms or rejects</small></div><i>→</i><div class="gnode">Frozen dataset<br><small>hashed, pre-registered</small></div></div>`, 'o', ui);
  guardBox.id = 'guard';
  const never = el('<b>Never sent to the AI</b><span>coordinates</span><span>point clouds</span><span>imagery</span><span>final verdicts</span>', 'o never', ui);
  const audit = el(`<div class="head"><span>audit log · example</span><span>model · prompt · result</span></div>` +
    ['layer mapping · prompt v3 · 46 proposals · 44 confirmed', 'field-note phases · prompt v5 · 118 proposals · 111 confirmed', 'skeptic round 1 · 6 objections · 6 checks run', 'quantogram + Monte Carlo · plain Python · no model']
      .map((x) => `<div class="row" style="grid-template-columns:1fr;height:44px"><span>${x}</span></div>`).join(''), 'o', ui);
  audit.id = 'records'; audit.style.top = '560px'; audit.style.width = '760px';
  const auditRows = [...audit.querySelectorAll('.row')];
  const outs = el('', 'cards', ui); outs.style.gridTemplateColumns = 'repeat(4, 1fr)'; outs.style.top = '330px';
  const OUT = OUTCOMES.map(([e, b]) => el(`<em>${e}</em><b>${b}</b><p>Publishable, because the test was fixed in advance.</p>`, 'card', outs));

  const tl = el('', 'o tl', ui); tl.style.opacity = 1;
  const TL = WEEKS.map(([e, b]) => el(`<em>${e}</em><div>${b}</div>`, 'bar', tl));

  const caption = el('', 'o', ui); caption.id = 'caption';
  const titlecard = el(`<div class="rule"></div><div class="big">${TITLE.replace('of ', 'of<br>')}</div><div class="sub">archaeology · lidar · ai</div>`, 'o', ui); titlecard.id = 'titlecard';
  const progress = el(TIMELINE.chapters.map(() => '<div class="seg"><i></i></div>').join(''), '', ui);
  progress.id = 'progress';
  progress.style.position = 'absolute';
  progress.style.gridTemplateColumns = TIMELINE.chapters.map((c) => `${c.dur.toFixed(2)}fr`).join(' ');
  const segs = [...progress.querySelectorAll('.seg')];

  const fade = el('', 'layer'); fade.id = 'fade';

  // ------------------------------------------------------------ quantogram data
  const xs = synth({ unit: 0.83, n: 120, sigma: 0.02, share: 0.7, seed: 7 });
  const qs = grid(0.3, 2.0, 0.0025);
  const ys = scan(xs, qs);
  const th = threshold(xs, qs, { sims: 120 });
  const pk = peak(qs, ys);

  // ------------------------------------------------------------ helpers
  const V = (x, y, z) => { const [wx, wz] = toWorld(x, z); return new THREE.Vector3(wx, y, wz); };
  const heroW = (dx, dy, dz) => V(HERO[0] + dx, heroBase.y + dy, HERO[1] + dz);
  const show = (node, o) => { node.style.opacity = o.toFixed(3); };
  const project = (v) => { camera.updateMatrixWorld(); const p = v.clone().project(camera); return [(p.x + 1) / 2 * W, (1 - p.y) / 2 * H, p.z]; };
  const setLabel = (node, worldV, o, dx = 0, dy = 0) => {
    const [x, y, z] = project(worldV);
    node.style.opacity = (z < 1 ? o : 0).toFixed(3);
    node.style.transform = `translate(${(x + dx).toFixed(1)}px, ${(y + dy).toFixed(1)}px) translate(-50%, -50%)`;
  };
  const camMove = (a, b, k, ta, tb, fov = 38) => {
    const e = easeIO(k);
    camera.position.copy(a).lerp(b, e);
    camera.lookAt(ta.clone().lerp(tb, e));
    camera.fov = fov; camera.updateProjectionMatrix();
  };
  const orbit = (c, r, y, a0, a1, k, ty = 0, fov = 38) => {
    const a = lerp(a0, a1, k);
    const [wx, wz] = toWorld(c[0] + Math.cos(a) * r, c[1] + Math.sin(a) * r);
    camera.position.set(wx, groundAt(c[0], c[1]) + y, wz);
    const [tx, tz] = toWorld(c[0], c[1]);
    camera.lookAt(tx, groundAt(c[0], c[1]) + ty, tz);
    camera.fov = fov; camera.updateProjectionMatrix();
  };
  const shadowsAt = (focus, extent) => {
    sun.target.position.copy(focus);
    sun.position.copy(focus).addScaledVector(sunDir, 6000);
    const c = sun.shadow.camera;
    c.left = -extent; c.right = extent; c.top = extent; c.bottom = -extent; c.near = 100; c.far = 14000;
    c.updateProjectionMatrix();
    sun.target.updateMatrixWorld();
  };
  const cueAt = (ch, i) => ch.cues[Math.min(i, ch.cues.length - 1)].at;

  const DIP = { curate: false, attack: false };   // chapters that continue the previous shot
  const allOverlays = [kicker, ctitle, note, ...stats, legend, ...hyps, pipeline, layersBox, compass, records, blind, stamp, ...sks, ...ROLE, lock, ...OUT, ...TL, vault, lockBadge, flow, stages, lineLegend, ...JOB, guardBox, never, audit, caption, titlecard];

  // ------------------------------------------------------------ frame
  function renderAt(t) {
    t = Math.max(0, Math.min(TIMELINE.duration - 1e-3, t));
    const tc = chapterAt(t);
    const ch = CHAPTERS[tc.index];
    const tau = t - tc.start;
    const k = tau / tc.dur;
    const id = ch.id;

    for (const o of allOverlays) o.style.opacity = '0';
    for (const d of dimObjs) { d.line.visible = false; d.el.style.opacity = '0'; }
    for (const r of rows) { r.style.opacity = '0'; r.querySelector('.v').style.filter = 'none'; r.querySelectorAll('.tag').forEach((x) => (x.style.opacity = '0')); }
    for (const l of [arcLabel, northLabel, axisLabel, transectLabel]) l.style.opacity = '0';
    north.visible = axis.visible = arcLine.visible = false;
    transect.visible = false;
    tiles.visible = false; cloudGround.visible = cloudAbove.visible = false; linesAncient.visible = linesModern.visible = false;
    city.visible = true; tileLabels.forEach((l) => (l.style.opacity = '0'));
    wire.visible = false; refGrid.visible = false;
    ctx.clearRect(0, 0, W, H);

    let lidar = 0, modernOn = 0, ancientHL = 0, heroOpacity = 1, dim = 0;
    let shadowFocus = V(0, 0, -300), shadowExtent = 3200;
    let noteText = '';

    // kicker / title
    if (id !== 'open') {
      kicker.textContent = ch.kicker; ctitle.textContent = ch.title;
      const endCard = id === 'next' ? tc.cues[tc.cues.length - 1].at + tc.cues[tc.cues.length - 1].dur + 0.6 : tc.dur;
      const o = win(tau, 0.25, Math.min(tc.dur - 0.2, endCard), 0.7, 0.6);
      show(kicker, o); show(ctitle, o);
    } else {
      kicker.textContent = ch.kicker; show(kicker, win(tau, 0.8, cueAt(tc, 2) + 1.5, 1.2, 0.8));
    }

    if (id === 'open') {
      camMove(V(1500, 980, 4300), V(420, 560, 1100), k, V(0, 60, -700), V(0, 20, -1050), 36);
      const c1 = cueAt(tc, 1);
      const o = win(tau, c1 + 0.2, cueAt(tc, 2) + 2.2, 0.8, 0.8);
      north.visible = axis.visible = arcLine.visible = o > 0.01;
      for (const l of [north, axis, arcLine]) l.material.opacity = o;
      setLabel(arcLabel, arcLabelPos, o, 0, -10);
      setLabel(northLabel, new THREE.Vector3(pw[0], py, pw[1] - 1500), o, -70, -16);
      setLabel(axisLabel, new THREE.Vector3(pw[0] + ax[0] * 1500, py, pw[1] + ax[1] * 1500), o, 70, -16);
      const last = tc.cues[tc.cues.length - 1];
      show(titlecard, win(tau, last.at + last.dur + 0.45, tc.dur, 0.9, 0.7));
      shadowFocus = V(100, 0, -600); shadowExtent = 2600;
    }

    if (id === 'lidar') {
      camMove(V(-3900, 3300, 3600), V(-2700, 2350, 2050), k, V(300, 0, 200), V(0, 0, -250), 40);
      lidar = ramp(tau, cueAt(tc, 0) + 0.4, 2.2);
      modernOn = ramp(tau, cueAt(tc, 1), 2.0);
      ancientHL = ramp(tau, cueAt(tc, 3) - 0.4, 1.2);
      stats.forEach((s, i) => show(s, win(tau, cueAt(tc, i) + 0.2, tc.dur - 0.3, 0.6, 0.5)));
      show(legend, win(tau, cueAt(tc, 3) - 0.3, tc.dur - 0.3));
      noteText = 'illustration · not survey data';
    }

    if (id === 'question') {
      orbit(HERO, lerp(118, 52, easeIO(k)), lerp(58, 17, easeIO(k)), -1.25, -0.25, easeIO(k), 5, 36);
      const c2 = cueAt(tc, 2);
      hyps.forEach((h, i) => show(h, win(tau, c2 + i * 0.32, tc.dur - 0.25, 0.45, 0.5)));
      const qo = ramp(tau, cueAt(tc, 3), 0.6);
      hyps.forEach((h) => (h.querySelector('.q').style.opacity = qo.toFixed(3)));
      shadowFocus = heroW(0, 0, 0); shadowExtent = 90;
      noteText = 'illustrative model · not survey data';
    }

    if (id === 'read') {
      const wk = easeIO(k);
      const [cx, cz] = toWorld(HERO[0] + 4, HERO[1] + 2);
      camera.position.copy(heroW(lerp(30, 20, wk), lerp(86, 64, wk), lerp(40, 30, wk)));
      camera.lookAt(cx, heroBase.y, cz);
      camera.fov = 38; camera.updateProjectionMatrix();
      heroOpacity = lerp(1, 0.1, ramp(tau, 0.4, 1.6));
      wire.visible = true;
      const wo = ramp(tau, 0.6, 1.4);
      wireLines.forEach((l) => (l.material.opacity = wo));
      gridLines.material.opacity = wo * 0.35;
      const rk = easeIO((tau - cueAt(tc, 1) - 0.3) / 3.2);
      wire.rotation.y = lerp(24 * DEG, 0, rk);
      refGrid.visible = true;
      refGrid.material.opacity = 0.55 * ramp(tau, cueAt(tc, 1), 1);
      show(pipeline, win(tau, cueAt(tc, 1) - 0.2, tc.dur - 0.3));
      show(layersBox, win(tau, 1.2, tc.dur - 0.3));
      show(compass, win(tau, cueAt(tc, 1), tc.dur - 0.3));
      shadowFocus = heroW(0, 0, 0); shadowExtent = 90;
      noteText = 'illustrative model';
    }

    if (id === 'extract' || id === 'curate') {
      const e = id === 'extract';
      const kk = e ? easeIO(k) * 0.6 : 0.6 + easeIO(k) * 0.4;
      orbit([HERO[0] + 8, HERO[1] - 9], lerp(54, 47, kk), lerp(24, 21, kk), lerp(0.12, 0.42, kk), lerp(0.12, 0.42, kk), 0, 6, 34);
      heroOpacity = e ? lerp(0.1, 1, ramp(tau, 0, 1.0)) : 1;
      show(records, e ? win(tau, cueAt(tc, 0) - 0.2, tc.dur + 5, 0.6) : 1);
      const n = dimObjs.length;
      dimObjs.forEach((d, i) => {
        const at = e ? cueAt(tc, 0) + 0.3 + i * ((cueAt(tc, 2) - cueAt(tc, 0) + 0.6) / n) : -1;
        const o = e ? ramp(tau, at, 0.35) : 1 - ramp(tau, tc.dur - 0.8, 0.6) * 0;
        d.line.visible = o > 0.01;
        d.line.material.opacity = o;
        const wp = d.mid.clone(); hero.group.localToWorld(wp);
        setLabel(d.el, wp, o * (e ? 1 : 0.85));
        rows[i].style.opacity = o.toFixed(3);
      });
      if (!e) {
        rows.forEach((r, i) => {
          const at = cueAt(tc, 0) + 0.2 + i * 0.32;
          r.querySelectorAll('.tag').forEach((x) => (x.style.opacity = ramp(tau, at, 0.3).toFixed(3)));
          r.querySelector('.v').style.filter = `blur(${(7 * ramp(tau, cueAt(tc, 1) - 0.2, 0.6)).toFixed(2)}px)`;
        });
        dimObjs.forEach((d) => (d.el.style.filter = `blur(${(6 * ramp(tau, cueAt(tc, 1) - 0.2, 0.6)).toFixed(2)}px)`));
        show(blind, ramp(tau, cueAt(tc, 1) - 0.2, 0.5));
        show(stamp, ramp(tau, cueAt(tc, 2) + 0.6, 0.35));
        stamp.style.transform = `rotate(-7deg) scale(${lerp(1.5, 1, easeIO((tau - cueAt(tc, 2) - 0.6) / 0.35)).toFixed(3)})`;
      } else {
        dimObjs.forEach((d) => (d.el.style.filter = 'none'));
      }
      shadowFocus = heroW(0, 0, 0); shadowExtent = 90;
      noteText = 'illustrative values';
    }

    if (id === 'test' || id === 'attack') {
      const tt = id === 'test';
      orbit(HERO, 70, 28, tt ? 0.4 + k * 0.5 : 0.9 + k * 0.4, tt ? 0.4 + k * 0.5 : 0.9 + k * 0.4, 0, 5, 40);
      dim = 0.78;
      shadowFocus = heroW(0, 0, 0); shadowExtent = 90;
      if (tt) { drawQuantogram(tc, tau); noteText = 'synthetic data · 120 lengths · 70% on an 83 cm unit · 2 cm noise'; }
      else {
        sks.forEach((s, i) => {
          show(s, win(tau, cueAt(tc, 0) + 0.2 + i * 0.28, tc.dur - 0.2, 0.4, 0.5));
          const flip = easeIO((tau - cueAt(tc, 2) - i * 0.18) / 0.7);
          s.querySelector('.front').style.transform = `rotateY(${(flip * 180).toFixed(1)}deg)`;
          s.querySelector('.back').style.transform = `rotateY(${(180 + flip * 180).toFixed(1)}deg)`;
        });
      }
    }

    if (id === 'store') {
      camMove(V(-300, 5600, 2900), V(-150, 4700, 2100), k, V(-300, 0, -200), V(-250, 0, -300), 38);
      lidar = 1; modernOn = 1;
      tiles.visible = true;
      tiles.material.opacity = ramp(tau, cueAt(tc, 0), 1.0) * 0.9;
      tileTags.forEach((tt, i) => {
        const [sx, sy] = project(tt.pos);
        const room = sx > 120 && sx < 960 && sy > 230 && sy < 790;
        setLabel(tileLabels[i], tt.pos, room ? ramp(tau, cueAt(tc, 2) + i * 0.08, 0.5) : 0);
      });
      show(lockBadge, win(tau, cueAt(tc, 1), tc.dur - 0.3));
      show(vault, win(tau, cueAt(tc, 2) - 0.3, tc.dur - 0.3));
      vaultRows.forEach((r, i) => (r.style.opacity = ramp(tau, cueAt(tc, 2) + i * 0.35, 0.3).toFixed(3)));
      show(flow, win(tau, cueAt(tc, 3) - 0.2, tc.dur - 0.3));
      noteText = 'illustrative file names and sizes';
      shadowFocus = V(0, 0, -300); shadowExtent = 3200;
    }

    if (id === 'process') {
      camMove(V(-1900, 820, 900), V(-1450, 640, 420), k, V(-700, 0, -260), V(-650, 0, -300), 40);
      lidar = 1;
      const c1 = cueAt(tc, 1), c2 = cueAt(tc, 2), c3 = cueAt(tc, 3);
      const pointsOn = 1 - ramp(tau, c1 + 1.6, 1.0);
      cloudGround.visible = cloudAbove.visible = pointsOn > 0.01;
      cloudGround.material.opacity = pointsOn;
      const drop = ramp(tau, c1 + 0.2, 1.2);
      cloudAbove.material.opacity = pointsOn * (1 - drop);
      cloudAbove.position.y = -drop * 10;
      city.visible = tau > c1 + 1.6;
      modernOn = ramp(tau, c1 + 1.6, 0.8);
      linesAncient.visible = linesModern.visible = tau > c2 - 0.2;
      const lo = ramp(tau, c2 - 0.1, 0.8);
      const cls = ramp(tau, c3 + 0.3, 1.0);
      linesAncient.material.opacity = lo; linesModern.material.opacity = lo * (1 - 0.45 * cls);
      linesAncient.material.color.set('#ece6d6').lerp(new THREE.Color('#2fd0a8'), cls);
      linesModern.material.color.set('#ece6d6').lerp(new THREE.Color('#c29a6b'), cls);
      ancientHL = cls * 0.6;
      const stage = tau < c1 ? 0 : tau < c1 + 1.6 ? 1 : tau < c1 + 2.8 ? 2 : tau < c2 ? 3 : tau < c3 ? 4 : 5;
      show(stages, win(tau, 0.4, tc.dur - 0.3));
      stageEls.forEach((e, i) => { e.className = i === stage ? 'on' : i < stage ? 'done' : ''; });
      if (tau > c2 - 0.2) drawHistogram(ramp(tau, c2, 0.8), cls);
      show(lineLegend, win(tau, c3 + 0.3, tc.dur - 0.3));
      noteText = 'illustration · not survey data';
      shadowFocus = V(-700, 0, -300); shadowExtent = 1400;
    }

    if (id === 'ai' || id === 'guard') {
      const g = id === 'guard';
      orbit(MOON, 1100, 520, g ? 2.6 : 2.0, g ? 3.0 : 2.5, k, 30, 36);
      dim = 0.55;
      shadowFocus = V(0, 0, -900); shadowExtent = 1800;
      if (!g) {
        JOB.forEach((j, i) => show(j, win(tau, cueAt(tc, i + 1) - 0.2, tc.dur - 0.3, 0.5, 0.5)));
      } else {
        show(guardBox, win(tau, cueAt(tc, 1) - 0.2, tc.dur - 0.3));
        guardBox.querySelector('.accent').style.boxShadow = `0 0 0 ${(3 * ramp(tau, cueAt(tc, 1) + 1.2, 0.5)).toFixed(1)}px var(--jade)`;
        show(never, win(tau, cueAt(tc, 2) - 0.2, tc.dur - 0.3));
        show(audit, win(tau, cueAt(tc, 3) - 0.3, tc.dur - 0.3));
        auditRows.forEach((r, i) => (r.style.opacity = ramp(tau, cueAt(tc, 3) + i * 0.45, 0.3).toFixed(3)));
        noteText = 'example log';
      }
    }

    if (id === 'city') {
      camMove(V(-2000, 1350, 650), V(-1600, 1050, 300), k, V(-760, 0, -330), V(-700, 0, -320), 40);
      lidar = 1; modernOn = 1;
      ancientHL = ramp(tau, cueAt(tc, 1) - 0.2, 1);
      transect.visible = true;
      transect.material.opacity = ramp(tau, cueAt(tc, 2) - 0.3, 1.2);
      setLabel(transectLabel, V(-820, groundAt(-820, tz) + 30, tz), transect.material.opacity, 0, -30);
      noteText = 'illustration · not survey data';
      shadowFocus = V(-700, 0, -300); shadowExtent = 1400;
    }

    if (id === 'team') {
      orbit(SUN, 820, 330, 1.9, 2.35, k, 20, 36);
      ROLE.forEach((r, i) => show(r, win(tau, cueAt(tc, i) + 0.2, tc.dur - 0.3, 0.5, 0.5)));
      show(lock, win(tau, cueAt(tc, 2), tc.dur - 0.3));
      dim = 0.35;
      shadowFocus = V(190, 0, -420); shadowExtent = 1200;
    }

    if (id === 'outcomes') {
      camMove(V(210, 150, 1450), V(120, 120, 1000), k, V(0, 30, -900), V(0, 25, -1000), 36);
      OUT.forEach((o, i) => show(o, win(tau, cueAt(tc, 0) + 0.2 + i * 1.15, tc.dur - 0.25, 0.45, 0.5)));
      dim = 0.4;
      shadowFocus = V(100, 0, 0); shadowExtent = 2200;
    }

    if (id === 'next') {
      camMove(V(800, 520, 2300), V(2700, 1650, 5600), k, V(0, 40, -700), V(0, 0, -900), 38);
      TL.forEach((b, i) => show(b, win(tau, (i < 2 ? cueAt(tc, i) : cueAt(tc, 2) + (i - 2) * 0.7) + 0.1, cueAt(tc, 2) + 3.4, 0.45, 0.6)));
      const last = tc.cues[tc.cues.length - 1];
      show(titlecard, ramp(tau, last.at + last.dur + 0.6, 1.0));
      dim = 0.35 * (1 - ramp(tau, last.at + last.dur + 0.4, 0.8));
      shadowFocus = V(100, 0, -500); shadowExtent = 3000;
    }

    // world state
    terU.uLidar.value = lidar; skyU.uLidar.value = lidar;
    terU.uModern.value = modernOn;
    scene.fog.color.copy(fogNatural).lerp(fogLidar, lidar);
    scene.fog.density = lerp(0.00011, 0.00008, lidar);
    modern.visible = modernOn > 0.01;
    modern.position.y = -(1 - modernOn) * 14;
    roadMat.opacity = modernOn;
    hMat.color.set('#c6b296').lerp(new THREE.Color('#5d5e60'), ancientHL * 0.9);
    cMat.color.copy(stoneBase).lerp(new THREE.Color('#2fd0a8'), ancientHL);
    cMat.emissive.set('#0d4a3c').multiplyScalar(ancientHL * 0.9);
    mats.stone.color.copy(stoneBase).lerp(new THREE.Color('#2fd0a8'), ancientHL * 0.85);
    mats.stone.emissive.set('#0d4a3c').multiplyScalar(ancientHL * 0.7);
    heroMats.stone.opacity = heroMats.stucco.opacity = heroOpacity;
    heroMats.stone.depthWrite = heroMats.stucco.depthWrite = heroOpacity > 0.95;
    sun.intensity = lerp(3.1, 1.2, lidar) * (1 - dim * 0.75);
    hemi.intensity = lerp(0.85, 1.4, lidar) * (1 - dim * 0.6);
    shadowsAt(shadowFocus, shadowExtent);

    // captions
    let cue = null, cueO = 0;
    for (let i = 0; i < tc.cues.length; i++) {
      const c = tc.cues[i];
      const end = i + 1 < tc.cues.length ? tc.cues[i + 1].at - 0.12 : (ch.hold ? c.at + c.dur + 0.3 : tc.dur - 0.25);
      if (tau >= c.at - 0.15 && tau < end) { cue = c; cueO = win(tau, c.at - 0.15, end, 0.3, 0.3); break; }
    }
    caption.textContent = cue ? cue.text : '';
    show(caption, cue ? cueO : 0);

    if (noteText) { note.textContent = noteText; show(note, win(tau, 0.6, tc.dur - 0.2)); }

    // chapter dips
    const dipIn = DIP[id] === false ? 0 : 1 - ramp(tau, 0, 0.45);
    const nextId = CHAPTERS[tc.index + 1]?.id;
    const dipOut = nextId && DIP[nextId] === false ? 0 : ramp(tau, tc.dur - 0.32, 0.32);
    let fadeO = Math.max(dipIn, dipOut);
    if (tc.index === 0) fadeO = Math.max(1 - ramp(tau, 0, 1.6), dipOut);
    if (tc.index === TIMELINE.chapters.length - 1) fadeO = ramp(tau, tc.dur - 1.0, 1.0);
    fade.style.opacity = fadeO.toFixed(3);

    // progress bar
    segs.forEach((s, i) => {
      const c = TIMELINE.chapters[i];
      s.classList.toggle('on', i === tc.index);
      s.firstChild.style.width = `${(clamp01((t - c.start) / c.dur) * 100).toFixed(2)}%`;
    });
    const lastCue = tc.cues[tc.cues.length - 1];
    const titleOn = id === 'open' || id === 'next' ? ramp(tau, lastCue.at + lastCue.dur, 0.6) : 0;
    progress.style.opacity = (1 - titleOn).toFixed(3);

    const fr = Math.floor(t * 30);
    grain.style.backgroundPosition = `${(fr * 73) % 256}px ${(fr * 151) % 256}px`;

    renderer.render(scene, camera);
  }

  // The Step 5 scene: lengths wrapped around a circle, and the quantogram they produce.
  function drawQuantogram(tc, tau) {
    const c0 = cueAt(tc, 0), c1 = cueAt(tc, 1), c2 = cueAt(tc, 2), c3 = cueAt(tc, 3);
    let q;
    const sweepStart = c2 + 0.4, sweepEnd = c3 - 0.9;
    if (tau < c1) q = lerp(0.61, 0.66, clamp01((tau - c0) / (c1 - c0)));
    else if (tau < c2) q = lerp(0.66, 0.83, easeIO((tau - c1) / (c2 - c1 - 0.6)));
    else if (tau < sweepStart) q = lerp(0.83, 0.3, easeIO((tau - c2) / 0.4));
    else if (tau < sweepEnd) q = lerp(0.3, 2.0, clamp01((tau - sweepStart) / (sweepEnd - sweepStart)));
    else if (tau < c3) q = lerp(2.0, 0.83, easeIO((tau - sweepEnd) / 0.9));
    else q = lerp(0.83, 0.415, easeIO((tau - c3 - 0.8) / 1.2));
    const swept = tau < sweepStart ? (tau < c2 ? 0 : 0) : clamp01((tau - sweepStart) / (sweepEnd - sweepStart));
    const curveO = tau < c2 ? 0 : 1;
    const o = win(tau, 0.3, tc.dur - 0.15, 0.6, 0.4);
    ctx.save();
    ctx.globalAlpha = o;

    // panel backdrops
    ctx.fillStyle = 'rgba(8,10,11,0.62)';
    roundRect(ctx, 96, 236, 760, 640, 12); ctx.fill();
    roundRect(ctx, 900, 236, 924, 640, 12); ctx.fill();

    // ring
    const cx = 476, cy = 560, R = 230;
    ctx.strokeStyle = 'rgba(236,230,214,0.35)'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = '#3fbfa0'; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(cx, cy - R - 18); ctx.lineTo(cx, cy - R + 18); ctx.stroke();
    ctx.fillStyle = '#3fbfa0'; ctx.font = '500 18px "JetBrains Mono"'; ctx.textAlign = 'center';
    ctx.fillText('0', cx, cy - R - 28);
    let sx = 0, sy = 0;
    for (let i = 0; i < xs.length; i++) {
      const f = (xs[i] / q) % 1;
      const a = f * Math.PI * 2 - Math.PI / 2;
      sx += Math.cos(f * Math.PI * 2); sy += Math.sin(f * Math.PI * 2);
      const rr = R + ((i % 9) - 4) * 7;
      ctx.fillStyle = 'rgba(236,230,214,0.82)';
      ctx.beginPath(); ctx.arc(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 4.2, 0, Math.PI * 2); ctx.fill();
    }
    // resultant: how strongly the lengths agree
    const mx = sy / xs.length, my = sx / xs.length;   // rotate so phase 0 points up
    ctx.strokeStyle = '#e0703f'; ctx.lineWidth = 5; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + mx * R, cy - my * R); ctx.stroke();
    ctx.fillStyle = '#ece6d6'; ctx.textAlign = 'center';
    ctx.font = '700 64px "JetBrains Mono"'; ctx.fillText(`${(q * 100).toFixed(1)}`, cx, cy + 14);
    ctx.font = '500 20px "JetBrains Mono"'; ctx.fillStyle = 'rgba(236,230,214,0.7)';
    ctx.fillText('candidate unit, cm', cx, cy + 50);
    ctx.textAlign = 'left'; ctx.font = '500 18px "JetBrains Mono"'; ctx.fillStyle = 'rgba(236,230,214,0.6)';
    ctx.fillText('EACH DOT = ONE LENGTH, WRAPPED AROUND THE UNIT', 126, 276);

    // chart
    const X0 = 980, X1 = 1780, Y0 = 800, Y1 = 350;
    const ymin = -4, ymax = 13;
    const px = (qq) => X0 + (qq - 0.3) / 1.7 * (X1 - X0);
    const py2 = (v) => Y0 - (v - ymin) / (ymax - ymin) * (Y0 - Y1);
    ctx.font = '500 18px "JetBrains Mono"'; ctx.fillStyle = 'rgba(236,230,214,0.6)';
    ctx.fillText('QUANTOGRAM SCORE φ(q)', 930, 274);
    ctx.strokeStyle = 'rgba(236,230,214,0.12)'; ctx.lineWidth = 1;
    for (let v = 0; v <= 12; v += 4) { ctx.beginPath(); ctx.moveTo(X0, py2(v)); ctx.lineTo(X1, py2(v)); ctx.stroke(); }
    ctx.fillStyle = 'rgba(236,230,214,0.55)'; ctx.textAlign = 'right';
    for (let v = 0; v <= 12; v += 4) ctx.fillText(String(v), X0 - 14, py2(v) + 6);
    ctx.textAlign = 'center';
    for (const cm of [30, 50, 80, 100, 150, 200]) ctx.fillText(`${cm}`, px(cm / 100), Y0 + 32);
    ctx.fillText('candidate unit q (cm)', (X0 + X1) / 2, Y0 + 62);
    // hypothesis markers
    for (const [qq, lab] of [[0.58, '58'], [0.805, '80.5'], [0.83, '83']]) {
      ctx.strokeStyle = 'rgba(134,169,200,0.5)'; ctx.setLineDash([4, 6]); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(px(qq), Y1 - 6); ctx.lineTo(px(qq), Y0); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = 'rgba(134,169,200,0.85)'; ctx.font = '500 15px "JetBrains Mono"';
      ctx.fillText(lab, px(qq) + (lab === '80.5' ? -22 : lab === '83' ? 16 : 0), Y1 - 14);
    }
    // threshold
    ctx.strokeStyle = 'rgba(224,112,63,0.85)'; ctx.setLineDash([10, 8]); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(X0, py2(th.p99)); ctx.lineTo(X1, py2(th.p99)); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = 'rgba(224,112,63,0.95)'; ctx.textAlign = 'right'; ctx.font = '500 16px "JetBrains Mono"';
    ctx.fillText('99% noise threshold (Monte Carlo)', X1, py2(th.p99) - 10);
    // curve
    if (curveO > 0) {
      const lastQ = 0.3 + swept * 1.7;
      ctx.strokeStyle = '#ece6d6'; ctx.lineWidth = 2.4; ctx.lineJoin = 'round';
      ctx.beginPath();
      for (let i = 0; i < qs.length && qs[i] <= lastQ; i++) {
        const X = px(qs[i]), Y = py2(Math.max(ymin, Math.min(ymax, ys[i])));
        i ? ctx.lineTo(X, Y) : ctx.moveTo(X, Y);
      }
      ctx.stroke();
      if (swept < 1) {
        ctx.fillStyle = '#e0703f';
        ctx.beginPath(); ctx.arc(px(lastQ), py2(Math.max(ymin, Math.min(ymax, valueAt(qs, ys, lastQ)))), 6, 0, Math.PI * 2); ctx.fill();
      }
      if (swept >= 1) {
        const po = ramp(tau, sweepEnd, 0.5);
        ctx.globalAlpha = o * po;
        ctx.fillStyle = '#e0703f';
        ctx.beginPath(); ctx.arc(px(pk.q), py2(pk.phi), 9, 0, Math.PI * 2); ctx.fill();
        ctx.textAlign = 'left'; ctx.font = '700 22px "JetBrains Mono"';
        ctx.fillText(`peak ${(pk.q * 100).toFixed(1)} cm`, px(pk.q) + 18, py2(pk.phi) + 4);
        const eo = ramp(tau, c3 + 0.3, 0.6);
        if (eo > 0) {
          const e = valueAt(qs, ys, 0.415);
          ctx.globalAlpha = o * eo;
          ctx.strokeStyle = '#86a9c8'; ctx.lineWidth = 2.5;
          ctx.beginPath(); ctx.arc(px(0.415), py2(e), 12, 0, Math.PI * 2); ctx.stroke();
          ctx.fillStyle = '#86a9c8'; ctx.textAlign = 'left';
          ctx.fillText('echo 41.5', px(0.415) + 18, py2(e) + 7);
        }
      }
    }
    ctx.restore();
  }

  // Step 8: orientation histogram of detected lines.
  function drawHistogram(o, cls) {
    const X0 = 1280, Y0 = 760, Wd = 540, Ht = 300;
    ctx.save();
    ctx.globalAlpha = o;
    ctx.fillStyle = 'rgba(8,10,11,0.72)';
    roundRect(ctx, X0 - 30, Y0 - Ht - 70, Wd + 60, Ht + 130, 10); ctx.fill();
    ctx.font = '500 17px "JetBrains Mono"'; ctx.fillStyle = 'rgba(236,230,214,0.7)'; ctx.textAlign = 'left';
    ctx.fillText('ORIENTATION OF DETECTED LINES', X0, Y0 - Ht - 36);
    const bw = Wd / 90;
    ctx.fillStyle = 'rgba(63,191,160,0.12)';
    for (const c of [15.5, 105.5]) ctx.fillRect(X0 + (c - 3) / 2 * bw, Y0 - Ht, 3 * bw, Ht);
    for (let i = 0; i < 90; i++) {
      const hgt = (hist[i] / histMax) * (Ht - 10);
      const deg = i * 2 + 1;
      const inWin = Math.abs(deg - 15.5) <= 3 || Math.abs(deg - 105.5) <= 3;
      ctx.fillStyle = inWin ? `rgba(${lerp(236, 47, cls) | 0},${lerp(230, 208, cls) | 0},${lerp(214, 168, cls) | 0},0.95)` : 'rgba(236,230,214,0.55)';
      ctx.fillRect(X0 + i * bw + 0.5, Y0 - hgt, bw - 1, hgt);
    }
    ctx.strokeStyle = 'rgba(236,230,214,0.35)'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(X0, Y0 + 0.5); ctx.lineTo(X0 + Wd, Y0 + 0.5); ctx.stroke();
    ctx.fillStyle = 'rgba(236,230,214,0.6)'; ctx.textAlign = 'center'; ctx.font = '500 15px "JetBrains Mono"';
    for (const d of [0, 45, 90, 135, 180]) ctx.fillText(`${d}°`, X0 + d / 2 * bw, Y0 + 26);
    ctx.fillStyle = '#e0703f'; ctx.font = '700 18px "JetBrains Mono"';
    ctx.fillText('15.5°', X0 + 15.5 / 2 * bw + 6, Y0 - Ht - 6);
    ctx.fillText('105.5°', X0 + 105.5 / 2 * bw, Y0 - Ht - 6);
    ctx.restore();
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath();
  }

  renderAt(0);
  return { renderAt, duration: TIMELINE.duration, renderer };
}
