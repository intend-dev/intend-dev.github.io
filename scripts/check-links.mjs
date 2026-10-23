import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import config from '../astro.config.mjs';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const base = (config.base ?? '').replace(/\/$/, '');

if (!existsSync(dist)) {
  console.error('No dist/ directory. Run npm run build first.');
  process.exit(1);
}

const files = [];
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else files.push(full);
  }
};
walk(dist);

const htmlFiles = files.filter((file) => file.endsWith('.html'));
const htmlByRoute = new Map();
const idsByFile = new Map();

for (const file of htmlFiles) {
  const html = readFileSync(file, 'utf8');
  const route = relative(dist, file).replaceAll('\\', '/');
  htmlByRoute.set(route, file);
  const ids = new Set();
  for (const match of html.matchAll(/\sid="([^"]+)"/g)) ids.add(match[1]);
  for (const match of html.matchAll(/\sname="([^"]+)"/g)) ids.add(match[1]);
  idsByFile.set(file, ids);
}

const errors = [];
const checked = new Set();

const resolveInternal = (href) => {
  let path = href.split('#')[0].split('?')[0];
  if (path.startsWith(base)) path = path.slice(base.length);
  if (path === '' || path === '/') path = '/index.html';
  else if (path.endsWith('/')) path = `${path}index.html`;
  else if (!path.includes('.')) path = `${path}/index.html`;
  return join(dist, path.replace(/^\//, ''));
};

for (const [route, file] of htmlByRoute) {
  const html = readFileSync(file, 'utf8');

  if (/\bTODO\b/.test(html)) errors.push(`${route}: contains TODO`);
  for (const match of html.matchAll(/href="([^"]*)"/g)) {
    const href = match[1];
    if (href === '#') {
      errors.push(`${route}: placeholder href="#"`);
      continue;
    }
    if (/^(mailto:|tel:|https?:|data:|\/\/)/.test(href)) continue;
    if (checked.has(`${route} ${href}`)) continue;
    checked.add(`${route} ${href}`);

    // A fragment-only href resolves against the page it appears on.
    const target = href.startsWith('#') ? file : resolveInternal(href);
    if (!existsSync(target)) {
      errors.push(`${route}: broken link ${href}`);
      continue;
    }
    if (!href.includes('#')) continue;
    const anchor = decodeURIComponent(href.split('#')[1]);
    if (!anchor) continue;
    const ids = idsByFile.get(target);
    if (ids && !ids.has(anchor)) {
      errors.push(`${route}: missing anchor #${anchor} in ${href}`);
    }
  }

  for (const match of html.matchAll(/src="([^"]*)"/g)) {
    const src = match[1];
    if (/^(https?:|data:|\/\/)/.test(src)) continue;
    const target = resolveInternal(src);
    if (!existsSync(target) || !statSync(target).isFile()) {
      errors.push(`${route}: broken src ${src}`);
    }
  }

  for (const match of html.matchAll(/<meta[^>]+http-equiv="refresh"[^>]*>/gi)) {
    const content = match[0].match(/content="([^"]*)"/i)?.[1] ?? '';
    const url = content.replace(/^\s*\d+\s*;\s*url=/i, '').trim();
    if (!url) {
      errors.push(`${route}: empty meta refresh`);
      continue;
    }
    if (/^(https?:|\/\/)/.test(url)) continue;
    const target = resolveInternal(url);
    if (!existsSync(target)) {
      errors.push(`${route}: broken redirect ${url}`);
      continue;
    }
    if (url.includes('#')) {
      const anchor = decodeURIComponent(url.split('#')[1]);
      const ids = idsByFile.get(target);
      if (anchor && ids && !ids.has(anchor)) {
        errors.push(`${route}: missing anchor #${anchor} in redirect ${url}`);
      }
    }
  }
}

// The six published slugs are deliberate: a seventh project also needs its redirect
// output, and that decision should be explicit rather than inferred.
const required = [
  'index.html',
  'work/index.html',
  'about/index.html',
  'cv/index.html',
  '404.html',
  'sitemap-index.xml',
  'sitemap-0.xml',
  'work/code-review-orchestrator/index.html',
  'work/ai-development-harness/index.html',
  'work/nasa-mission-rag/index.html',
  'work/semantic-retrieval-benchmark/index.html',
  'work/multimodal-moderation/index.html',
  'work/ai-budget-predicter/index.html',
];

for (const file of required) {
  if (!existsSync(join(dist, file))) errors.push(`missing required output dist/${file}`);
}

const stubRoutes = [...htmlByRoute.keys()].filter((route) => /^(?:about\/index\.html|work\/[^/]+\/index\.html)$/.test(route));
for (const stub of stubRoutes) {
  const html = readFileSync(join(dist, stub), 'utf8');
  if (!/http-equiv="refresh"/i.test(html)) errors.push(`${stub}: missing meta refresh fallback`);
  if (!/noindex/i.test(html)) errors.push(`${stub}: missing noindex`);
}

const pdf = join(root, 'public', 'Francisco_Mateus_CV.pdf');
if (!existsSync(pdf)) errors.push('missing public/Francisco_Mateus_CV.pdf');

if (errors.length > 0) {
  console.error(`Link check failed with ${errors.length} problem(s):`);
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(`Link check OK: ${htmlFiles.length} HTML files, ${checked.size} internal links, anchors and assets resolved.`);
