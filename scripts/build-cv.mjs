import { createServer } from 'node:http';
import { existsSync, statSync, mkdirSync, renameSync, unlinkSync, readFileSync } from 'node:fs';
import config from '../astro.config.mjs';
import { spawn } from 'node:child_process';
import { extname, join, normalize, resolve, sep } from 'node:path';
import { findChrome } from './lib/chrome.mjs';
import { hasPhone, stripHtml } from './lib/facts.mjs';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const pdf = join(root, 'public', 'Francisco_Mateus_CV.pdf');
const base = (config.base ?? '').replace(/\/$/, '');
const site = String(config.site ?? '').replace(/\/$/, '');
// Chromium writes the document URL, so every internal hash link must be absolute
// before printing or the PDF annotation points at the print server.
const absolutizeInternalAnchors = (html) => html.replaceAll(`href="${base}/#`, `href="${site}${base}/#`);
const pdfTemp = `${pdf}.tmp`;

if (!existsSync(dist)) {
  console.error('Run npm run build first.');
  process.exit(1);
}

const cvHtmlPath = join(dist, 'cv', 'index.html');
if (!existsSync(cvHtmlPath)) {
  console.error('dist/cv/index.html is missing. Run npm run build first.');
  process.exit(1);
}
if (hasPhone(stripHtml(readFileSync(cvHtmlPath, 'utf8')))) {
  console.error('Phone number found in /cv/. Refusing to generate the public PDF.');
  process.exit(1);
}

const types = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf',
  '.woff2': 'font/woff2',
  '.png': 'image/png',
  '.xml': 'application/xml; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
};

const server = createServer((request, response) => {
  try {
    let pathname = decodeURIComponent(new URL(request.url ?? '/', 'http://127.0.0.1').pathname);
    if (pathname.startsWith(base)) pathname = pathname.slice(base.length);
    if (pathname === '' || pathname === '/') pathname = '/index.html';
    if (pathname.endsWith('/')) pathname += 'index.html';

    const file = normalize(join(dist, pathname.replace(/^\//, '')));
    if (!file.startsWith(dist + sep)) {
      response.writeHead(403).end('Forbidden');
      return;
    }
    if (!existsSync(file) || !statSync(file).isFile()) {
      response.writeHead(404).end('Not found');
      return;
    }

    response.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream' });
    if (extname(file) === '.html') response.end(absolutizeInternalAnchors(readFileSync(file, 'utf8')));
    else response.end(readFileSync(file));
  } catch (error) {
    response.writeHead(500).end('Server error');
  }
});

const chrome = findChrome();

if (!chrome) {
  console.error('No Chromium found. Set CHROME_PATH or print /cv/ to PDF manually.');
  process.exit(1);
}

mkdirSync(join(root, '.cache', 'chrome-cv'), { recursive: true });
mkdirSync(join(root, 'public'), { recursive: true });
await new Promise((resolveListen, rejectListen) => {
  server.once('error', rejectListen);
  server.listen(0, '127.0.0.1', resolveListen);
});

const port = server.address().port;
if (existsSync(pdfTemp)) unlinkSync(pdfTemp);

const args = [
  '--headless=new',
  '--disable-gpu',
  '--no-sandbox',
  '--no-pdf-header-footer',
  '--run-all-compositor-stages-before-draw',
  '--virtual-time-budget=2000',
  `--user-data-dir=${join(root, '.cache', 'chrome-cv')}`,
  `--print-to-pdf=${pdfTemp}`,
  `http://127.0.0.1:${port}${base}/cv/`,
];

const exitCode = await new Promise((resolveExit) => {
  const child = spawn(chrome, args, { stdio: 'ignore' });
  child.once('error', () => resolveExit(1));
  child.once('exit', (code) => resolveExit(code ?? 1));
});

server.close();

if (exitCode !== 0) {
  if (existsSync(pdfTemp)) unlinkSync(pdfTemp);
  console.error(`Chromium exited with code ${exitCode}.`);
  process.exit(1);
}

if (!existsSync(pdfTemp) || statSync(pdfTemp).size < 20_000) {
  if (existsSync(pdfTemp)) unlinkSync(pdfTemp);
  console.error('PDF was not written or is smaller than 20,000 bytes.');
  process.exit(1);
}

renameSync(pdfTemp, pdf);
console.log('Wrote public/Francisco_Mateus_CV.pdf');
