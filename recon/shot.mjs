// Screenshots of the reconstruction viewer in headless Chrome, for checking views.
//   node recon/shot.mjs avenue:today:then city:bare:then sun:then:then:ev ...
// Each argument is view:left:right[:ev][:one]. Writes recon-data/shots/<arg>.png and prints frame times.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { default: puppeteer } = await import(pathToFileURL(path.join(ROOT, 'render', 'node_modules', 'puppeteer-core', 'lib', 'esm', 'puppeteer', 'puppeteer-core.js')).href);
const DOCS = path.join(ROOT, 'docs');
const OUT = path.join(ROOT, 'recon-data', 'shots');
fs.mkdirSync(OUT, { recursive: true });
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.json': 'application/json', '.png': 'image/png', '.css': 'text/css' };
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  const f = path.join(DOCS, p);
  if (!f.startsWith(DOCS) || !fs.existsSync(f)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': TYPES[path.extname(f)] || 'application/octet-stream' });
  fs.createReadStream(f).pipe(res);
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const W = Number(process.env.W || 1600), H = Number(process.env.H || 900);
const browser = await puppeteer.launch({
  executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true,
  args: ['--ignore-gpu-blocklist', '--enable-gpu-rasterization', '--use-angle=metal', `--window-size=${W},${H}`],
  defaultViewport: { width: W, height: H, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
page.on('pageerror', (e) => console.log('[pageerror]', e.message));
page.on('console', (m) => { if (m.type() === 'error') console.log('[console]', m.text()); });
await page.goto(`http://127.0.0.1:${server.address().port}/reconstruction/`, { waitUntil: 'networkidle0', timeout: 120000 });
await page.waitForFunction('window.__recon', { timeout: 120000 });
for (const arg of process.argv.slice(2)) {
  const [view, left, right, ...flags] = arg.split(':');
  const ms = await page.evaluate(async (view, left, right, ev, one) => {
    const R = window.__recon;
    R.setLeft(left); R.setRight(right); R.setSingle(one); R.setEvidence(ev); R.goTo(view, true);
    document.querySelector('#info').hidden = true;
    await new Promise((r) => setTimeout(r, 1500));
    const t0 = performance.now(); let n = 0;
    await new Promise((r) => { const f = () => { n++; if (performance.now() - t0 < 1500) requestAnimationFrame(f); else r(); }; requestAnimationFrame(f); });
    return (performance.now() - t0) / n;
  }, view, left, right, flags.includes('ev'), flags.includes('one'));
  await page.screenshot({ path: path.join(OUT, `${arg.replace(/:/g, '_')}.png`) });
  console.log(`${arg}: ${ms.toFixed(1)} ms/frame`);
}
await browser.close();
server.close();
