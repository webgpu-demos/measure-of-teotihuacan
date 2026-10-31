// Teotihuacan from public lidar: terrain from INEGI's 1.5 m LiDAR models,
// monuments sized from that terrain, and every reconstructed part tagged with
// the evidence behind it. Built by recon/build_data.py (data) and this file (view).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const DEG = Math.PI / 180;
const Y0 = 2280;                     // world y = elevation - Y0

export const CLASSES = {
  measured: { label: 'Measured in lidar', color: '#2fb59a', note: 'Footprint and height from the public 1.5 m lidar (as the monument stands today).' },
  excavated: { label: 'Excavated plan', color: '#e2b13c', note: 'Known from excavation and published plans; not resolvable in public lidar.' },
  inferred: { label: 'Inferred', color: '#d9534f', note: 'Temples, roofs and finishes reconstructed by analogy; nothing survives to measure.' },
  lidar: { label: 'Lidar mound (unverified)', color: '#8b7cf6', note: 'A compact, compound-sized mound found automatically in the public lidar. Not checked on the ground.' },
  illustrative: { label: 'Illustrative', color: '#9aa3ad', note: 'Placed by rule to show the city’s density. Positions are not data.' },
};

const NATURAL = {
  stucco: '#ece4d4', red: '#9b4234', plaza: '#ddd1ba', temple: '#efe7d8', roof: '#d6c9b0', street: '#d8ccb3',
};

// ------------------------------------------------------------------ data
async function loadHeights(url, meta, L) {
  const img = await new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = rej; i.src = url; });
  const c = document.createElement('canvas');
  c.width = img.width; c.height = img.height;
  const g = c.getContext('2d', { willReadFrequently: true });
  g.drawImage(img, 0, 0);
  const px = g.getImageData(0, 0, img.width, img.height).data;
  const n = img.width * img.height;
  const h = new Float32Array(n);
  for (let i = 0; i < n; i++) h[i] = meta.base + (px[i * 4] * 256 + px[i * 4 + 1]) / meta.scale - Y0;
  return { h, w: img.width, hgt: img.height, L };
}

function sampler(layers) {
  // Bilinear lookup in the finest layer that covers (x, y); y is local north.
  return (x, y, which = 'dtm') => {
    for (const lay of layers) {
      const L = lay.L;
      if (x < L.x0 || x > L.x1 || y < L.y0 || y > L.y1) continue;
      const a = lay[which] || lay.dtm;
      const fx = (x - L.x0) / L.res - 0.5, fy = (L.y1 - y) / L.res - 0.5;
      const ix = Math.max(0, Math.min(a.w - 2, Math.floor(fx))), iy = Math.max(0, Math.min(a.hgt - 2, Math.floor(fy)));
      const tx = Math.min(1, Math.max(0, fx - ix)), ty = Math.min(1, Math.max(0, fy - iy));
      const i = iy * a.w + ix;
      const v = a.h[i] * (1 - tx) * (1 - ty) + a.h[i + 1] * tx * (1 - ty) + a.h[i + a.w] * (1 - tx) * ty + a.h[i + a.w + 1] * tx * ty;
      return v;
    }
    return 0;
  };
}

function halfTexture(a) {
  const d = new Uint16Array(a.h.length);
  for (let i = 0; i < a.h.length; i++) d[i] = THREE.DataUtils.toHalfFloat(a.h[i]);
  const t = new THREE.DataTexture(d, a.w, a.hgt, THREE.RedFormat, THREE.HalfFloatType);
  t.minFilter = t.magFilter = THREE.LinearFilter;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  t.needsUpdate = true;
  return t;
}

// ------------------------------------------------------------------ terrain
function terrainMaterial(lay, hole) {
  const L = lay.L;
  const u = {
    uDtm: { value: halfTexture(lay.dtm) }, uDsm: { value: halfTexture(lay.dsm || lay.dtm) },
    uRect: { value: new THREE.Vector4(L.x0, L.x1, L.y0, L.y1) },
    uTexel: { value: new THREE.Vector2(1 / lay.dtm.w, 1 / lay.dtm.hgt) }, uRes: { value: L.res },
    uHole: { value: hole ? new THREE.Vector4(hole.x0, hole.x1, hole.y0, hole.y1) : new THREE.Vector4(1, -1, 1, -1) },
    uDsmMix: { value: 0 }, uLidar: { value: 0 },
  };
  const m = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.96, metalness: 0 });
  m.userData.u = u;
  m.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, u);
    const decl = `uniform sampler2D uDtm; uniform sampler2D uDsm; uniform vec4 uRect; uniform vec2 uTexel; uniform float uRes;
      uniform vec4 uHole; uniform float uDsmMix; uniform float uLidar; varying vec2 vTex; varying vec3 vW;
      float hAt(vec2 t) { return mix(texture2D(uDtm, t).r, texture2D(uDsm, t).r, uDsmMix); }`;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', `#include <common>\n${decl}`)
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vec4 wp0 = modelMatrix * vec4(transformed, 1.0);
        vTex = vec2((wp0.x - uRect.x) / (uRect.y - uRect.x), 1.0 - ((-wp0.z) - uRect.z) / (uRect.w - uRect.z));
        transformed.y = hAt(vTex);
        vW = vec3(wp0.x, transformed.y, wp0.z);`);
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', `#include <common>\n${decl}
        float h21(vec2 p){ p = fract(p*vec2(123.34, 456.21)); p += dot(p, p+45.32); return fract(p.x*p.y); }
        float vn(vec2 p){ vec2 i=floor(p), f=fract(p); vec2 w=f*f*(3.0-2.0*f);
          return mix(mix(h21(i),h21(i+vec2(1,0)),w.x), mix(h21(i+vec2(0,1)),h21(i+vec2(1,1)),w.x), w.y); }
        vec3 tNormal() {
          float hl = hAt(vTex - vec2(uTexel.x, 0.0)), hr = hAt(vTex + vec2(uTexel.x, 0.0));
          float hu = hAt(vTex - vec2(0.0, uTexel.y)), hd = hAt(vTex + vec2(0.0, uTexel.y));
          return normalize(vec3(-(hr - hl) / (2.0 * uRes), 1.0, -(hd - hu) / (2.0 * uRes)));
        }`)
      .replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
        { vec2 lp = vec2(vW.x, -vW.z); if (lp.x > uHole.x && lp.x < uHole.y && lp.y > uHole.z && lp.y < uHole.w) discard; }`)
      .replace('#include <color_fragment>', `#include <color_fragment>
        {
          vec3 n = tNormal();
          float slope = 1.0 - n.y;
          float a = vn(vW.xz / 160.0), b = vn(vW.xz / 23.0);
          vec3 earth = mix(vec3(0.55, 0.43, 0.30), vec3(0.64, 0.53, 0.38), a);
          vec3 grass = mix(vec3(0.42, 0.42, 0.25), vec3(0.52, 0.50, 0.30), b);
          vec3 c = mix(earth, grass, smoothstep(0.45, 0.75, a * 0.6 + b * 0.4) * 0.6);
          c = mix(c, vec3(0.45, 0.40, 0.35), smoothstep(0.15, 0.45, slope));
          diffuseColor.rgb = c * c * 1.15;
        }`)
      .replace('#include <normal_fragment_maps>', `#include <normal_fragment_maps>
        normal = normalize((viewMatrix * vec4(tNormal(), 0.0)).xyz);`)
      .replace('#include <tonemapping_fragment>', `
        {
          vec3 n = tNormal();
          float hs = 0.5 * max(dot(n, normalize(vec3(-0.6, 0.75, -0.4))), 0.0) + 0.3 * max(dot(n, normalize(vec3(0.55, 0.7, -0.45))), 0.0)
                   + 0.2 * max(dot(n, normalize(vec3(0.0, 1.0, 0.6))), 0.0);
          float above = texture2D(uDsm, vTex).r - texture2D(uDtm, vTex).r;
          vec3 base = mix(vec3(0.42, 0.44, 0.44), vec3(0.80, 0.79, 0.75), clamp((vW.y + 20.0) / 240.0, 0.0, 1.0));
          vec3 lid = base * (0.1 + 1.5 * pow(hs, 1.6));
          lid = mix(lid, vec3(0.93, 0.66, 0.42) * (0.3 + 1.1 * hs), smoothstep(1.2, 3.0, above) * uDsmMix * 0.8);
          gl_FragColor.rgb = mix(gl_FragColor.rgb, lid * lid, uLidar);
        }
        #include <tonemapping_fragment>`);
  };
  return m;
}

function terrainMesh(lay, hole, seg) {
  const L = lay.L;
  const geo = new THREE.PlaneGeometry(L.x1 - L.x0, L.y1 - L.y0, seg[0], seg[1]);
  geo.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geo, terrainMaterial(lay, hole));
  mesh.position.set((L.x0 + L.x1) / 2, 0, -(L.y0 + L.y1) / 2);
  mesh.receiveShadow = true;
  mesh.frustumCulled = false;
  return mesh;
}

// ------------------------------------------------------------------ geometry helpers
function frustumGeo(w0, d0, w1, d1, h) {
  const p = [[-w0 / 2, 0, -d0 / 2], [w0 / 2, 0, -d0 / 2], [w0 / 2, 0, d0 / 2], [-w0 / 2, 0, d0 / 2],
    [-w1 / 2, h, -d1 / 2], [w1 / 2, h, -d1 / 2], [w1 / 2, h, d1 / 2], [-w1 / 2, h, d1 / 2]];
  const quads = [[4, 7, 6, 5], [0, 4, 5, 1], [1, 5, 6, 2], [2, 6, 7, 3], [3, 7, 4, 0]];
  const pos = [];
  for (const [a, b, c, d] of quads) pos.push(...p[a], ...p[b], ...p[c], ...p[a], ...p[c], ...p[d]);
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute('uv', new THREE.Float32BufferAttribute(new Float32Array(pos.length / 3 * 2), 2));
  g.computeVertexNormals();
  return g;
}
const boxGeo = (w, h, d, x = 0, y = 0, z = 0) => { const g = new THREE.BoxGeometry(w, h, d).toNonIndexed(); g.translate(x, y + h / 2, z); return g; };
const colorize = (g, hex) => {
  const c = new THREE.Color(hex);
  const n = g.attributes.position.count;
  const a = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
  g.setAttribute('color', new THREE.BufferAttribute(a, 3));
  return g;
};

// ------------------------------------------------------------------ the city
export async function createRecon(canvas, opts = {}) {
  const meta = await (await fetch('data/meta.json')).json();
  const S = await (await fetch('data/sites.json')).json();
  const mounds = await (await fetch('data/mounds.json')).json();
  const ML = meta.layers;
  const [cdtm, cdsm, xdtm, xdsm, hz] = await Promise.all([
    loadHeights('data/core_dtm.png', meta, ML.core), loadHeights('data/core_dsm.png', meta, ML.core),
    loadHeights('data/context_dtm.png', meta, ML.context), loadHeights('data/context_dsm.png', meta, ML.context),
    loadHeights('data/horizon.png', meta, ML.horizon),
  ]);
  const layers = [
    { L: ML.core, dtm: cdtm, dsm: cdsm }, { L: ML.context, dtm: xdtm, dsm: xdsm }, { L: ML.horizon, dtm: hz, dsm: hz },
  ];
  const heightAt = sampler(layers);
  const G = meta.grid_deg * DEG;
  const gridToLocal = (u, v) => [u * Math.cos(G) + v * Math.sin(G), -u * Math.sin(G) + v * Math.cos(G)];
  const localToGrid = (x, y) => [x * Math.cos(G) - y * Math.sin(G), x * Math.sin(G) + y * Math.cos(G)];

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: !!opts.preserve });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, opts.maxDpr || 2));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setScissorTest(true);

  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0xcdb59a, 0.00009);
  const camera = new THREE.PerspectiveCamera(42, 1, 3, 90000);

  const skyU = { uLidar: { value: 0 }, uSun: { value: new THREE.Vector3(-0.78, 0.33, 0.52).normalize() } };
  scene.add(new THREE.Mesh(new THREE.SphereGeometry(60000, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false, uniforms: skyU,
    vertexShader: 'varying vec3 vD; void main(){ vD = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }',
    fragmentShader: `varying vec3 vD; uniform float uLidar; uniform vec3 uSun;
      void main(){ float y = clamp(vD.y, -0.2, 1.0);
        vec3 c = mix(vec3(0.96,0.76,0.55), vec3(0.20,0.36,0.62), pow(clamp(y*1.7,0.0,1.0), 0.55));
        float s = max(dot(normalize(vD), uSun), 0.0); c += vec3(1.0,0.78,0.5) * (pow(s, 18.0)*0.45 + pow(s, 600.0)*2.0);
        vec3 d = mix(vec3(0.05,0.07,0.08), vec3(0.11,0.14,0.16), clamp(y*2.0,0.0,1.0));
        gl_FragColor = vec4(mix(c, d, uLidar), 1.0); }`,
  })));

  const sunDir = skyU.uSun.value.clone();
  const sun = new THREE.DirectionalLight(0xffe2bd, 3.0);
  sun.castShadow = true;
  sun.shadow.mapSize.set(opts.lowPower ? 2048 : 4096, opts.lowPower ? 2048 : 4096);
  sun.shadow.bias = -0.0005; sun.shadow.normalBias = 0.8;
  scene.add(sun, sun.target);
  const hemi = new THREE.HemisphereLight(0xb9cbe6, 0x7a5c40, 0.9);
  scene.add(hemi);

  // Terrain: horizon (40 m) under context (12 m) under core (3 m); each outer layer cuts a hole for the inner one.
  const lowP = !!opts.lowPower;
  const tCore = terrainMesh(layers[0], null, lowP ? [467, 500] : [1400, 1500]);
  // Holes are shrunk a little so neighbouring layers overlap instead of leaving slivers of sky.
  const shrink = (L, m) => ({ x0: L.x0 + m, x1: L.x1 - m, y0: L.y0 + m, y1: L.y1 - m });
  const tCtx = terrainMesh(layers[1], shrink(ML.core, 6), lowP ? [245, 290] : [488, 579]);
  const tHor = terrainMesh(layers[2], shrink(ML.context, 40), [200, 200]);
  scene.add(tCore, tCtx, tHor);
  const terrains = [tCore, tCtx, tHor];

  // ---------------------------------------------------------------- reconstruction
  const recon = new THREE.Group();
  scene.add(recon);
  const pickables = [];
  const mats = {};
  const natMat = (key) => (mats['n' + key] ||= new THREE.MeshStandardMaterial({ color: NATURAL[key], roughness: 0.88, flatShading: true }));
  const evMat = (cls) => (mats['e' + cls] ||= new THREE.MeshStandardMaterial({ color: CLASSES[cls].color, roughness: 0.75, flatShading: true }));
  const counts = Object.fromEntries(Object.keys(CLASSES).map((k) => [k, 0]));

  function addPart(parent, geo, role, cls, info, pos) {
    const m = new THREE.Mesh(geo, natMat(role));
    if (pos) m.position.copy(pos);
    m.castShadow = m.receiveShadow = true;
    m.userData = { role, cls, info };
    parent.add(m);
    pickables.push(m);
    counts[cls]++;
    return m;
  }
  function placeGroup(cx, cy, baseY, rotExtra = 0) {
    const g = new THREE.Group();
    g.position.set(cx, baseY, -cy);
    g.rotation.y = -G + rotExtra;
    recon.add(g);
    return g;
  }

  // Talud-tablero platform; front faces -u (west) by default, rotate the group for other fronts.
  function stepped(g, spec, info) {
    const { w, d, heights, talud = 0.7, slope = 0.8, overhang = 0.3, margin = 1.5, cls = 'measured', stair, temple } = spec;
    let y = 0, w0 = w, d0 = d;
    heights.forEach((th) => {
      const tH = th * talud, bH = th - tH;
      const w1 = w0 - 2 * tH * slope, d1 = d0 - 2 * tH * slope;
      const t = addPart(g, frustumGeo(w0, d0, w1, d1, tH), 'stucco', cls, info);
      t.position.y = y;
      if (bH > 0.05) {
        addPart(g, boxGeo(w1 + 2 * overhang, bH, d1 + 2 * overhang, 0, y + tH), 'red', cls, info);
        addPart(g, boxGeo(w1 + 2 * overhang + 0.1, 0.18, d1 + 2 * overhang + 0.1, 0, y + th - 0.12), 'stucco', cls, info);
      }
      y += th;
      w0 = w1 - 2 * margin; d0 = d1 - 2 * margin;
    });
    if (stair) {
      const run = Math.max(6, (w - w0) / 2 + stair.ext);
      const steps = 22;
      for (let k = 0; k < steps; k++) {
        const hgt = ((k + 1) / steps) * y;
        const x0 = -w / 2 - stair.ext + (k / steps) * run;
        addPart(g, boxGeo(run / steps + 0.05, hgt, stair.width, x0 + run / steps / 2), 'stucco', cls, info);
      }
    }
    if (temple) {
      const tw = Math.min(w0 * 0.75, temple.w), td = Math.min(d0 * 0.75, temple.d);
      addPart(g, boxGeo(tw, temple.h, td, 0, y), 'temple', 'inferred', { ...info, part: 'Temple on the summit (inferred)' });
      addPart(g, boxGeo(tw + 1.2, 0.8, td + 1.2, 0, y + temple.h), 'roof', 'inferred', { ...info, part: 'Roof (inferred)' });
    }
    return y;
  }

  const site = (k) => S.sites[k]?.measured;
  const fmt = (v) => `${v.toFixed(1)} m`;
  const measuredInfo = (k, title, extra = {}) => {
    const m = site(k);
    return { title, osm: S.sites[k]?.osm, rows: [['Footprint', `${fmt(m.w)} × ${fmt(m.d)}`, 'measured'], ['Height above surrounding ground', fmt(m.height), 'measured'], ...(extra.rows || [])], note: extra.note };
  };

  // Pyramid of the Sun: faces west onto the avenue
  {
    const m = site('sun');
    const info = measuredInfo('sun', 'Pyramid of the Sun', { rows: [['Temple on the summit', 'not preserved', 'inferred']], note: 'Restored 1905–10, when several metres of the outer layer were removed; the stepped profile measured here is the restored one.' });
    const g = placeGroup(m.cx, m.cy, m.base - Y0 - 0.5);
    const H = m.height + 0.5;
    stepped(g, { w: m.w * 1.03, d: m.d * 1.03, heights: [0.3, 0.27, 0.22, 0.13, 0.08].map((f) => f * H), talud: 0.92, slope: 0.95, overhang: 0.2, margin: 2.5, stair: { width: 20, ext: 6 }, temple: { w: 22, d: 22, h: 7 } }, info);
    // Adosada platform in front
    const ga = placeGroup(...gridToLocal(localToGrid(m.cx, m.cy)[0] - m.w / 2 - 30, localToGrid(m.cx, m.cy)[1]), heightAt(...gridToLocal(localToGrid(m.cx, m.cy)[0] - m.w / 2 - 30, localToGrid(m.cx, m.cy)[1])) - 0.5);
    stepped(ga, { w: 46, d: 70, heights: [6, 4.5], talud: 0.6, slope: 0.4, cls: 'excavated' }, { title: 'Adosada of the Sun Pyramid', rows: [['Form', 'from excavation plans', 'excavated']] });
    // Enclosing platform
    const [su, sv] = localToGrid(m.cx, m.cy);
    for (const [du, dv, len, horiz] of [[0, 175, 360, true], [0, -175, 360, true], [175, 0, 330, false]]) {
      const [x, y] = gridToLocal(su + du, sv + dv);
      const gp = placeGroup(x, y, heightAt(x, y) - 0.6);
      addPart(gp, frustumGeo(horiz ? len : 26, horiz ? 26 : len, horiz ? len - 6 : 20, horiz ? 20 : len - 6, 3.4), 'stucco', 'excavated', { title: 'Sun Pyramid enclosure', rows: [['Form', 'platform visible as a low square in the lidar', 'excavated']] });
    }
  }

  // Pyramid of the Moon: faces south down the avenue
  {
    const m = site('moon');
    const info = measuredInfo('moon', 'Pyramid of the Moon', { rows: [['Temple on the summit', 'not preserved', 'inferred']], note: 'Built in seven stages between about 100 and 400 CE; excavations inside found offerings and burials.' });
    const g = placeGroup(m.cx, m.cy, m.base - Y0 - 0.5, Math.PI / 2);
    const H = m.height + 0.5;
    stepped(g, { w: m.d * 1.03, d: m.w * 1.03, heights: [0.3, 0.26, 0.22, 0.14, 0.08].map((f) => f * H), talud: 0.85, slope: 0.85, overhang: 0.3, margin: 2, stair: { width: 18, ext: 5 }, temple: { w: 18, d: 18, h: 6 } }, info);
  }

  // Ciudadela: raised banks with 15 temples, and the Feathered Serpent Pyramid
  {
    const c = S.derived.ciudadela;
    const [cu, cv] = localToGrid(c.cx, c.cy);
    const ground = heightAt(c.cx, c.cy);
    const bankH = Math.max(4, c.bank_top - Y0 - ground);
    const infoB = { title: 'La Ciudadela', rows: [['Enclosure', `${fmt(c.w)} × ${fmt(c.d)}`, 'measured'], ['Bank height', fmt(bankH), 'measured'], ['Temples on the banks', '15 (4 N, 4 S, 4 W, 3 E), counted in the lidar', 'measured']] };
    const bw = 42;
    const banks = [[0, c.d / 2 - bw / 2, c.w, bw], [0, -c.d / 2 + bw / 2, c.w, bw], [-c.w / 2 + bw / 2, 0, bw, c.d - 2 * bw], [c.w / 2 - bw * 0.65, 0, bw * 1.3, c.d - 2 * bw]];
    for (const [du, dv, w, d] of banks) {
      const [x, y] = gridToLocal(cu + du, cv + dv);
      const g = placeGroup(x, y, ground - 0.5);
      addPart(g, frustumGeo(w, d, w - 5, d - 5, bankH + 0.5), 'stucco', 'measured', infoB);
    }
    // Each temple's stair faces into the plaza: north bank faces south, south faces north, west faces east, east faces west.
    const temples = [];
    for (let i = 0; i < 4; i++) temples.push([-c.w / 2 + 70 + i * ((c.w - 140) / 3), c.d / 2 - bw / 2, Math.PI / 2], [-c.w / 2 + 70 + i * ((c.w - 140) / 3), -c.d / 2 + bw / 2, -Math.PI / 2]);
    for (let i = 0; i < 4; i++) temples.push([-c.w / 2 + bw / 2, -c.d / 2 + 85 + i * ((c.d - 170) / 3), Math.PI]);
    for (let i = 0; i < 3; i++) temples.push([c.w / 2 - bw * 0.65, -c.d / 2 + 110 + i * ((c.d - 220) / 2), 0]);
    for (const [du, dv, rot] of temples) {
      const [x, y] = gridToLocal(cu + du, cv + dv);
      const g = placeGroup(x, y, ground + bankH - 0.2, rot);
      stepped(g, { w: 22, d: 22, heights: [3.5, 3, 2.5], talud: 0.55, slope: 0.45, overhang: 0.35, margin: 1, cls: 'measured', stair: { width: 6, ext: 2 }, temple: { w: 9, d: 9, h: 3.6 } }, { ...infoB, title: 'Ciudadela bank temple' });
    }
    const f = site('fsp');
    const gF = placeGroup(f.cx, f.cy, f.base - Y0 - 0.4);
    stepped(gF, { w: f.w, d: f.d, heights: Array(6).fill((f.height + 0.4) / 6), talud: 0.38, slope: 0.55, overhang: 0.6, margin: 1.4, stair: { width: 12, ext: 4 }, temple: { w: 14, d: 14, h: 5 } },
      measuredInfo('fsp', 'Feathered Serpent Pyramid', { rows: [['Talud-tablero facade', 'excavated and restored', 'excavated'], ['Temple on the summit', 'not preserved', 'inferred']], note: 'Its facade carries the carved heads of the Feathered Serpent and a headdress figure, the city’s most elaborate sculpture.' }));
    const a = site('fsp-adosada');
    const gA = placeGroup(a.cx, a.cy, a.base - Y0 - 0.4);
    stepped(gA, { w: a.w, d: a.d, heights: [a.height * 0.34, a.height * 0.33, a.height * 0.33 + 0.4], talud: 0.5, slope: 0.5, overhang: 0.5, margin: 1.2, stair: { width: 14, ext: 3 } }, measuredInfo('fsp-adosada', 'Adosada platform', { note: 'Built against the Feathered Serpent Pyramid’s front, partly covering its facade.' }));
  }

  // Street of the Dead: plaster floor, cross walls detected along the axis, flanking platforms
  const ax = S.derived.street;
  const crossWalls = [];
  {
    const step = 3;
    const vs = [], hs = [];
    for (let v = ax.v0; v <= ax.v1; v += step) { vs.push(v); hs.push(heightAt(...gridToLocal(ax.u, v))); }
    const k = Math.round(45 / step);
    for (let i = k; i < hs.length - k; i++) {
      let s = 0; for (let j = -k; j <= k; j++) s += hs[i + j];
      const r = hs[i] - s / (2 * k + 1);
      const isMax = hs[i] >= hs[i - 1] && hs[i] >= hs[i + 1];
      if (isMax && r > 0.9 && (!crossWalls.length || vs[i] - crossWalls[crossWalls.length - 1].v > 24)) crossWalls.push({ v: vs[i], r });
    }
    const floor = [];
    const sv = [];
    for (let v = ax.v0 - 60; v <= Math.min(ax.v1 + 120, 1000); v += 8) sv.push(v);
    const half = 22;
    const pos = [];
    for (let i = 0; i < sv.length - 1; i++) {
      const pts = [[ax.u - half, sv[i]], [ax.u + half, sv[i]], [ax.u + half, sv[i + 1]], [ax.u - half, sv[i + 1]]].map(([u, v]) => { const [x, y] = gridToLocal(u, v); return [x, heightAt(x, y) + 0.25, -y]; });
      pos.push(...pts[0], ...pts[1], ...pts[2], ...pts[0], ...pts[2], ...pts[3]);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.computeVertexNormals();
    addPart(recon, g, 'street', 'excavated', { title: 'Street of the Dead', rows: [['Axis', `${(meta.grid_deg + ax.residual_deg).toFixed(2)}° east of north (fitted)`, 'measured'], ['Width', 'about 40 m (published)', 'excavated'], ['Cross walls', `${crossWalls.length} found as bumps along the axis`, 'measured']] });
    for (const c of crossWalls) {
      const [x, y] = gridToLocal(ax.u, c.v);
      const gp = placeGroup(x, y, heightAt(x, y) - 0.4);
      addPart(gp, boxGeo(46, Math.min(5, c.r + 1.6), 5), 'stucco', 'measured', { title: 'Cross wall on the Street of the Dead', rows: [['Height of the bump in the lidar', fmt(c.r), 'measured']] });
    }
    for (const side of [-1, 1]) {
      for (let v = -760; v < 760; v += 48) {
        if (side > 0 && v > 150 && v < 470) continue;   // the Sun Pyramid's plaza
        const [x, y] = gridToLocal(ax.u + side * 33, v + 20);
        const gp = placeGroup(x, y, heightAt(x, y) - 0.5);
        addPart(gp, frustumGeo(18, 40, 15, 37, 3.2), 'stucco', 'excavated', { title: 'Platforms flanking the avenue', rows: [['Form', 'continuous platforms between sunken plazas (published plans)', 'excavated']] });
      }
    }
  }

  // Moon Plaza floor
  {
    const m = site('moon');
    const [mu, mv] = localToGrid(m.cx, m.cy);
    const [x, y] = gridToLocal(mu, mv - m.d / 2 - 85);
    const gp = placeGroup(x, y, heightAt(x, y) - 0.2);
    addPart(gp, boxGeo(210, 0.6, 140), 'plaza', 'excavated', { title: 'Moon Plaza', rows: [['Form', 'plastered plaza ringed by temple platforms', 'excavated']] });
  }

  // Excavated apartment compounds and palaces, on their OSM footprints
  const compoundParts = (w, d, h, roles) => {
    const t = 1.4, gs = [];
    gs.push(colorize(boxGeo(w, h, t, 0, 0, -d / 2 + t / 2), roles.stucco), colorize(boxGeo(w, h, t, 0, 0, d / 2 - t / 2), roles.stucco));
    gs.push(colorize(boxGeo(t, h, d, -w / 2 + t / 2, 0, 0), roles.stucco), colorize(boxGeo(t, h, d, w / 2 - t / 2, 0, 0), roles.stucco));
    gs.push(colorize(boxGeo(w + 0.4, 4.9, d + 0.4, 0, -4, 0), roles.red));   // foundation and red base band, deep enough for sloping ground
    const pw = w * 0.36, pd = d * 0.36;
    const r = [[0, -(pd / 2 + (d / 2 - pd / 2) / 2), w - 3, d / 2 - pd / 2 - 1.5], [0, pd / 2 + (d / 2 - pd / 2) / 2, w - 3, d / 2 - pd / 2 - 1.5],
      [-(pw / 2 + (w / 2 - pw / 2) / 2), 0, w / 2 - pw / 2 - 1.5, pd], [pw / 2 + (w / 2 - pw / 2) / 2, 0, w / 2 - pw / 2 - 1.5, pd]];
    for (const [x, z, rw, rd] of r) gs.push(colorize(boxGeo(rw, h - 0.4, rd, x, 0, z), roles.stucco), colorize(boxGeo(rw + 0.6, 0.5, rd + 0.6, x, h - 0.4, z), roles.roof));
    gs.push(colorize(boxGeo(pw * 0.3, 2.2, pd * 0.3, pw * 0.25, 0, 0), roles.red));
    return mergeGeometries(gs, false);
  };
  const vcMat = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, flatShading: true });
  const named = { tetitla: 'Tetitla', atetelco: 'Atetelco', zacuala: 'Zacuala', yayahuala: 'Yayahuala', tepantitla: 'Tepantitla', xalla: 'Xalla', quetzalpapalotl: 'Palace of Quetzalpapálotl', 'plaza-oeste': 'West Plaza complex', viking: 'Viking Group', superpuestos: 'Superimposed Buildings', sacerdotes: 'Priests’ residences', 'palacio-sol': 'Palace of the Sun' };
  for (const [k, title] of Object.entries(named)) {
    const m = site(k);
    if (!m) continue;
    const g = placeGroup(m.cx, m.cy, heightAt(m.cx, m.cy) - 0.4);
    const geo = compoundParts(Math.max(24, m.w), Math.max(24, m.d), 3.6, { stucco: NATURAL.stucco, red: NATURAL.red, roof: NATURAL.roof });
    const mesh = addPart(g, geo, 'stucco', 'excavated', { title, osm: S.sites[k].osm, rows: [['Footprint', `${fmt(m.w)} × ${fmt(m.d)} (OSM outline)`, 'measured'], ['Rooms, patios and walls', 'excavated plan, simplified', 'excavated'], ['Roofs', 'flat roofs on beams', 'inferred']] });
    mesh.material = vcMat;
    mesh.userData.vc = true;
  }

  // Instanced compounds: lidar mounds (unverified) and an illustrative fill
  const keepOut = [];
  for (const k of ['sun', 'moon', 'fsp', 'fsp-adosada', ...Object.keys(named)]) { const m = site(k); if (m) keepOut.push([m.cx, m.cy, Math.max(m.w, m.d) / 2 + 25]); }
  { const c = S.derived.ciudadela; keepOut.push([c.cx, c.cy, Math.max(c.w, c.d) / 2 + 30]); }
  const onStreet = (x, y, half) => { const [u, v] = localToGrid(x, y); return Math.abs(u - ax.u) < half && v > ax.v0 - 60 && v < 1000; };
  const clear = (x, y, pad = 0) => keepOut.every(([kx, ky, r]) => Math.hypot(x - kx, y - ky) > r + pad) && !onStreet(x, y, 24 + Math.max(0, pad));
  const zone = S.sites.zone.poly;
  const inZone = (x, y) => { let c = false; for (let i = 0, j = zone.length - 1; i < zone.length; j = i++) { const [xi, yi] = zone[i], [xj, yj] = zone[j]; if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) c = !c; } return c; };

  const unitCompound = compoundParts(1, 1, 3.4, { stucco: NATURAL.stucco, red: NATURAL.red, roof: NATURAL.roof });
  // The unit geometry is 1 x 1 in plan with real heights; instances scale x and z only.
  const unitTemple = (() => { const gs = []; let y = 0, w = 1; for (const th of [0.45, 0.35, 0.2]) { const g = frustumGeo(w, w, w - 0.16, w - 0.16, th * 0.7); g.translate(0, y, 0); gs.push(colorize(g, NATURAL.stucco)); gs.push(colorize(boxGeo(w - 0.12, th * 0.3, w - 0.12, 0, y + th * 0.7), NATURAL.red)); y += th; w -= 0.24; } gs.push(colorize(boxGeo(0.34, 0.25, 0.34, 0, y), NATURAL.temple)); return mergeGeometries(gs, false); })();

  const lidarCompounds = [], lidarTemples = [], fill = [];
  for (const md of mounds) {
    if (!clear(md.x, md.y, -10)) continue;
    const item = { x: md.x, y: md.y, s: Math.min(70, Math.max(22, md.size * 1.05)), h: md.height };
    (md.size <= 40 ? lidarTemples : lidarCompounds).push(item);
  }
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let u = -2500; u <= 2500; u += 62) {
    for (let v = -2700; v <= 2700; v += 62) {
      const ru = (u - 60) / 2350, rv = (v + 40) / 2650;
      const r2 = ru * ru + rv * rv;
      if (r2 > 1) continue;
      const p = 0.62 * Math.sqrt(1 - r2) + 0.05;
      if (rnd() > p) continue;
      const [x, y] = gridToLocal(u + (rnd() - 0.5) * 8, v + (rnd() - 0.5) * 8);
      if (inZone(x, y) || !clear(x, y, 10)) continue;
      fill.push({ x, y, s: 38 + rnd() * 20 });
    }
  }
  const instanced = (geo, items, cls, scaleY, info) => {
    const mesh = new THREE.InstancedMesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.9, flatShading: true }), items.length);
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), -G);
    const m4 = new THREE.Matrix4();
    items.forEach((it, i) => {
      const yb = heightAt(it.x, it.y) - 0.4;
      m4.compose(new THREE.Vector3(it.x, yb, -it.y), q, new THREE.Vector3(it.s, scaleY(it), it.s));
      mesh.setMatrixAt(i, m4);
    });
    mesh.castShadow = mesh.receiveShadow = true;
    mesh.userData = { cls, info, items, instanced: true, vc: true };
    recon.add(mesh);
    pickables.push(mesh);
    counts[cls] += items.length;
    return mesh;
  };
  instanced(unitCompound, lidarCompounds, 'lidar', () => 1, { title: 'Compound on a lidar mound', rows: [['Mound', 'found automatically in the public lidar', 'lidar'], ['Building form', 'standard apartment compound', 'inferred']] });
  instanced(unitTemple, lidarTemples, 'lidar', (it) => Math.min(9, Math.max(3, it.h * 2.2)), { title: 'Platform on a lidar mound', rows: [['Mound', 'found automatically in the public lidar', 'lidar'], ['Building form', 'talud-tablero temple platform', 'inferred']] });
  instanced(unitCompound, fill, 'illustrative', () => 1, { title: 'Apartment compound (illustrative)', rows: [['Position', 'placed by rule within the city’s extent', 'illustrative'], ['Building form', 'standard apartment compound', 'inferred']] });

  // ---------------------------------------------------------------- views and state
  const MODES = {
    today: { label: 'Today', sub: 'lidar surface', dsm: 1, lidar: 1, recon: false },
    bare: { label: 'Bare earth', sub: 'lidar ground', dsm: 0, lidar: 1, recon: false },
    then: { label: 'c. 450 CE', sub: 'reconstruction', dsm: 0, lidar: 0, recon: true },
  };
  let evidence = false;
  function applyMode(key) {
    const M = MODES[key];
    for (const t of terrains) { t.material.userData.u.uDsmMix.value = M.dsm; t.material.userData.u.uLidar.value = M.lidar; }
    recon.visible = M.recon;
    skyU.uLidar.value = M.lidar;
    scene.fog.color.set(M.lidar ? 0x0d1214 : 0xd4bb9c);
    scene.fog.density = M.lidar ? 0.00006 : 0.00009;
    sun.intensity = M.lidar ? 1.2 : 3.0;
    hemi.intensity = M.lidar ? 1.3 : 0.9;
  }
  function setEvidence(on) {
    evidence = on;
    recon.traverse((o) => {
      if (!o.isMesh || !o.userData.cls) return;
      if (o.userData.instanced || o.userData.vc) {
        o.material.vertexColors = !on;
        o.material.color.set(on ? CLASSES[o.userData.cls].color : 0xffffff);
        o.material.needsUpdate = true;
      } else {
        o.material = on ? evMat(o.userData.cls) : natMat(o.userData.role);
      }
    });
  }

  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI * 0.49;
  controls.minDistance = 40; controls.maxDistance = 16000;
  const P = (x, y, h) => new THREE.Vector3(x, h, -y);
  const sunM = site('sun'), moonM = site('moon'), cM = S.derived.ciudadela;
  const VIEWS = {
    avenue: { label: 'Down the avenue', pos: (() => { const [x, y] = gridToLocal(ax.u + 60, ax.v0 - 260); return P(x, y, heightAt(x, y) + 120); })(), target: (() => { const [x, y] = gridToLocal(ax.u, 700); return P(x, y, 30); })() },
    moon: { label: 'Moon Plaza', pos: (() => { const [u, v] = localToGrid(moonM.cx, moonM.cy); const [x, y] = gridToLocal(u + 40, v - 520); return P(x, y, 75); })(), target: P(moonM.cx, moonM.cy, moonM.base - Y0 + 18) },
    sun: { label: 'Pyramid of the Sun', pos: (() => { const [u, v] = localToGrid(sunM.cx, sunM.cy); const [x, y] = gridToLocal(u - 420, v - 260); return P(x, y, 140); })(), target: P(sunM.cx, sunM.cy, sunM.base - Y0 + 20) },
    ciudadela: { label: 'La Ciudadela', pos: (() => { const [u, v] = localToGrid(cM.cx, cM.cy); const [x, y] = gridToLocal(u - 330, v + 300); return P(x, y, 170); })(), target: P(cM.cx, cM.cy, 2) },
    city: { label: 'The whole city', pos: P(-1500, -2700, 1350), target: P(60, 250, 0) },
  };
  let fly = null;
  function goTo(key, instant) {
    const V = VIEWS[key];
    if (instant) { camera.position.copy(V.pos); controls.target.copy(V.target); controls.update(); return; }
    fly = { t0: performance.now(), dur: 1800, p0: camera.position.clone(), t0v: controls.target.clone(), p1: V.pos, t1: V.target };
  }

  // Picking
  const ray = new THREE.Raycaster();
  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects(pickables, false)[0];
    if (!hit) return null;
    const o = hit.object;
    const info = o.userData.info;
    return { ...info, cls: o.userData.cls, part: info.part };
  }

  // Render: one or two views split by a divider (fraction of width)
  let split = 0.5, left = 'today', right = 'then', single = false;
  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  }
  window.addEventListener('resize', resize);
  resize();

  function updateShadow() {
    const t = controls.target;
    const dist = camera.position.distanceTo(t);
    const ext = Math.min(3000, Math.max(250, dist * 1.1));
    sun.target.position.copy(t);
    sun.position.copy(t).addScaledVector(sunDir, 5000);
    const c = sun.shadow.camera;
    c.left = -ext; c.right = ext; c.top = ext; c.bottom = -ext; c.near = 100; c.far = 12000;
    c.updateProjectionMatrix();
    sun.target.updateMatrixWorld();
  }
  function frame() {
    if (fly) {
      const k = Math.min(1, (performance.now() - fly.t0) / fly.dur);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      camera.position.lerpVectors(fly.p0, fly.p1, e);
      controls.target.lerpVectors(fly.t0v, fly.t1, e);
      if (k >= 1) fly = null;
    }
    controls.update();
    updateShadow();
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (single) {
      renderer.setScissor(0, 0, w, h); renderer.setViewport(0, 0, w, h);
      applyMode(right); renderer.render(scene, camera);
    } else {
      const sx = Math.round(w * split);
      renderer.setViewport(0, 0, w, h);
      renderer.setScissor(0, 0, sx, h); applyMode(left); renderer.render(scene, camera);
      renderer.setScissor(sx, 0, w - sx, h); applyMode(right); renderer.render(scene, camera);
    }
  }
  goTo(opts.view || 'avenue', true);
  renderer.setAnimationLoop(opts.manual ? null : frame);

  return {
    MODES, VIEWS, CLASSES, counts, crossWalls: crossWalls.length,
    setSplit: (f) => { split = Math.max(0, Math.min(1, f)); }, getSplit: () => split,
    setLeft: (k) => { left = k; }, setRight: (k) => { right = k; }, setSingle: (s) => { single = s; },
    setEvidence, goTo, pick, frame, renderer, camera, controls,
  };
}
