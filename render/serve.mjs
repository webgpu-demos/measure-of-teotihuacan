// Static server for docs/ with HTTP range support, so the film can seek locally.
// Usage: node render/serve.mjs [port]
// render/render.mjs and recon/shot.mjs import docsHandler and CHROME from here.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'docs');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json', '.mp4': 'video/mp4', '.m4a': 'audio/mp4', '.jpg': 'image/jpeg', '.png': 'image/png' };

export function docsHandler(req, res) {
  let p = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  if (p.endsWith('/')) p += 'index.html';
  const file = path.join(WEB, p);
  if (!file.startsWith(WEB) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); res.end(); return; }
  const size = fs.statSync(file).size;
  const type = TYPES[path.extname(file)] || 'application/octet-stream';
  const m = /bytes=(\d*)-(\d*)/.exec(req.headers.range || '');
  if (m) {
    const start = m[1] ? Number(m[1]) : size - Number(m[2]);
    const end = m[1] && m[2] ? Number(m[2]) : size - 1;
    res.writeHead(206, { 'content-type': type, 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${size}`, 'content-length': end - start + 1, 'cache-control': 'no-store' });
    fs.createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { 'content-type': type, 'accept-ranges': 'bytes', 'content-length': size, 'cache-control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url))) {
  const PORT = Number(process.argv[2] || 8731);
  http.createServer(docsHandler).listen(PORT, '127.0.0.1', () => console.log(`serving ${WEB} on http://127.0.0.1:${PORT}`));
}
