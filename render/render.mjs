// Render docs/film.html to video, frame by frame, with the system Chrome.
//
//   node render/render.mjs                    full film -> render/out/film-video.mp4
//   node render/render.mjs --stills 3,40,90   PNG stills -> render/out/still-<t>.png
//   node render/render.mjs --from 120 --to 140 --out render/out/clip.mp4
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import puppeteer from 'puppeteer-core';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WEB = path.join(ROOT, 'docs');
const OUT = path.join(ROOT, 'render', 'out');
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FPS = 30;

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith('--')) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
  return acc;
}, []));

const TYPES = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json', '.m4a': 'audio/mp4', '.mp4': 'video/mp4', '.png': 'image/png', '.jpg': 'image/jpeg' };
const server = http.createServer((req, res) => {
  const p = path.join(WEB, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!p.startsWith(WEB) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': TYPES[path.extname(p)] || 'application/octet-stream' });
  fs.createReadStream(p).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const port = server.address().port;

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--window-size=1920,1080', '--hide-scrollbars', '--mute-audio', '--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=metal', '--disable-background-timer-throttling', '--disable-renderer-backgrounding'],
  defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') console.log('[page]', m.text()); });
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
await page.goto(`http://127.0.0.1:${port}/film.html?render`, { waitUntil: 'networkidle0', timeout: 120000 });
await page.waitForFunction('window.__ready === true', { timeout: 120000 });
const duration = await page.evaluate(() => window.__duration);
const gl = await page.evaluate(() => {
  const g = window.__film.renderer.getContext();
  const ext = g.getExtension('WEBGL_debug_renderer_info');
  return ext ? g.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'unknown';
});
console.log(`film ${duration.toFixed(2)}s · renderer: ${gl}`);
fs.mkdirSync(OUT, { recursive: true });

const frameAt = async (t) => {
  await page.evaluate((tt) => window.__film.renderAt(tt), t);
  return page.screenshot({ type: 'jpeg', quality: 92, optimizeForSpeed: true });
};

if (args.eval) {
  // Debug: node render/render.mjs --eval "104|expression"
  const [t, expr] = String(args.eval).split('|');
  await page.evaluate((tt) => window.__film.renderAt(tt), Number(t));
  console.log(JSON.stringify(await page.evaluate(expr), null, 1));
} else if (args.stills) {
  for (const s of String(args.stills).split(',').map(Number)) {
    await page.evaluate((tt) => window.__film.renderAt(tt), s);
    await page.screenshot({ path: path.join(OUT, `still-${s.toFixed(1)}.png`) });
    console.log('still', s);
  }
} else {
  const from = Number(args.from || 0), to = Math.min(duration, Number(args.to || duration));
  const out = args.out ? path.resolve(args.out) : path.join(OUT, 'film-video.mp4');
  const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
    '-c:v', 'libx264', '-preset', 'medium', '-crf', '19', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', out], { stdio: ['pipe', 'inherit', 'inherit'] });
  const n = Math.round((to - from) * FPS);
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    const buf = await frameAt(from + i / FPS);
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    if (i % 150 === 0) {
      const el = (Date.now() - t0) / 1000;
      console.log(`frame ${i}/${n} · ${(from + i / FPS).toFixed(1)}s · ${(el / Math.max(1, i)).toFixed(3)} s/frame · eta ${((n - i) * el / Math.max(1, i) / 60).toFixed(1)} min`);
    }
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  console.log('wrote', out, `${((Date.now() - t0) / 1000).toFixed(0)}s`);
}
await browser.close();
server.close();
