import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import config from '../astro.config.mjs';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const problems = [];

if (!existsSync(dist)) {
  console.error('No dist/ directory. Run npm run build first.');
  process.exit(1);
}

const site = String(config.site ?? '').replace(/\/$/, '');
const base = `/${String(config.base ?? '').replace(/^\/|\/$/g, '')}`;
const origin = `${site}${base === '/' ? '' : base}`;

const requireFile = (name, label) => {
  const file = join(dist, name);
  if (!existsSync(file)) {
    problems.push(`${label} is missing: dist/${name}`);
    return '';
  }
  return readFileSync(file, 'utf8');
};

const sitemap = requireFile('sitemap-0.xml', 'Sitemap');
const index = requireFile('sitemap-index.xml', 'Sitemap index');
const robots = requireFile('robots.txt', 'robots.txt');

const locs = [...sitemap.matchAll(/<loc>([^<]*)<\/loc>/g)].map((match) => match[1].trim());
// The six published slugs are the same deliberate list as in check-links.mjs.
const studySlugs = [
  'ai-budget-predicter',
  'ai-development-harness',
  'code-review-orchestrator',
  'multimodal-moderation',
  'nasa-mission-rag',
  'semantic-retrieval-benchmark',
];
const expected = [origin + '/', origin + '/work/', ...studySlugs.map((slug) => `${origin}/work/${slug}/`)];
const missing = expected.filter((url) => !locs.includes(url));
const extra = locs.filter((url) => !expected.includes(url));
if (locs.length !== expected.length) problems.push(`sitemap-0.xml has ${locs.length} URLs, expected ${expected.length}`);
if (missing.length > 0) problems.push(`sitemap-0.xml is missing ${missing.join(', ')}`);
if (extra.length > 0) problems.push(`sitemap-0.xml has unexpected URLs ${extra.join(', ')}`);

const unslashed = locs.filter((url) => !url.endsWith('/'));
if (unslashed.length > 0) {
  problems.push(`sitemap-0.xml lists URLs without a trailing slash (${unslashed.join(', ')})`);
}

const indexLocs = [...index.matchAll(/<loc>([^<]*)<\/loc>/g)].map((match) => match[1].trim());
if (indexLocs.length !== 1 || indexLocs[0] !== `${origin}/sitemap-0.xml`) {
  problems.push(`sitemap-index.xml points at [${indexLocs.join(', ')}], expected ${origin}/sitemap-0.xml`);
}

if (!/^Allow:\s*\/\s*$/m.test(robots)) problems.push('robots.txt has no "Allow: /" line');
if (!robots.includes(`Sitemap: ${origin}/sitemap-index.xml`)) {
  problems.push(`robots.txt does not point at ${origin}/sitemap-index.xml`);
}

const files = [];
const walk = (dir) => {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (entry.name.endsWith('.html')) files.push(full);
  }
};
walk(dist);

const canonicalFor = (route) => {
  if (route === 'work/index.html') return `${origin}/work/`;
  if (/^work\/[^/]+\/index\.html$/.test(route)) return `${origin}/${route.replace(/index\.html$/, '')}`;
  if (route === 'about/index.html') return `${origin}/`;
  if (route === 'index.html' || route === '404.html') return `${origin}/`;
  if (route === 'cv/index.html') return `${origin}/cv/`;
  return null;
};

for (const file of files) {
  const route = relative(dist, file).replaceAll('\\', '/');
  const html = readFileSync(file, 'utf8');
  const canonicals = [...html.matchAll(/<link\b[^>]*rel="canonical"[^>]*>/gi)].map((match) => match[0]);
  if (canonicals.length !== 1) {
    problems.push(`${route}: ${canonicals.length} canonical links, expected exactly 1`);
    continue;
  }
  const href = canonicals[0].match(/href="([^"]*)"/)?.[1] ?? '';
  if (!href.startsWith('https://')) problems.push(`${route}: canonical ${href} is not absolute`);
  if (/localhost|127\.0\.0\.1/.test(href)) problems.push(`${route}: canonical ${href} points at a local server`);
  if (!href.startsWith(`${origin}/`)) problems.push(`${route}: canonical ${href} is outside ${origin}/`);

  const expectedCanonical = canonicalFor(route);
  if (!expectedCanonical) problems.push(`${route}: unexpected page, no canonical rule`);
  else if (href !== expectedCanonical) problems.push(`${route}: canonical ${href}, expected ${expectedCanonical}`);
}

if (problems.length > 0) {
  console.error('SEO check failed:');
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

console.log(`SEO check OK: ${expected.length} sitemap URLs, sitemap index and robots point at ${origin}, ${files.length} canonicals absolute.`);
