import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { hasPhone, stripHtml } from './lib/facts.mjs';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const problems = [];

// The outline check follows src/data/copy.json instead of repeating its strings, so a
// copy edit updates one file and the check still verifies that the built page matches it.
const copy = JSON.parse(readFileSync(join(root, 'src', 'data', 'copy.json'), 'utf8'));

if (!existsSync(dist)) {
  console.error('No dist/ directory. Run npm run build first.');
  process.exit(1);
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

const EXPECTED = new Map([
  [
    'index.html',
    [
      copy.sections.projects,
      copy.sections.capabilities,
      copy.story.title,
      copy.sections.contact,
    ],
  ],
  [
    'work/index.html',
    [
      copy.work.heading,
    ],
  ],
  [
    'cv/index.html',
    [
      copy.cv.summary,
      copy.sections.experience,
      copy.cv.projects,
      copy.sections.skills,
      copy.sections.education,
    ],
  ],
  ['404.html', null],
]);


const ALIASES = new Set(['about/index.html']);
const isAliasRoute = (route) => ALIASES.has(route);
// The study pages are real pages with content-driven headings; they take the generic
// rules (one h1, ordered levels, unique ids) without a pinned outline, which would
// otherwise duplicate check-facts' section assertions against source text.
const isStudyRoute = (route) => /^work\/[^/]+\/index\.html$/.test(route);

const plain = stripHtml;

const headingSource = (html) =>
  html
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<script\b[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[\s\S]*?<\/style>/gi, ' ')
    .replace(/<pre\b[\s\S]*?<\/pre>/gi, ' ')
    .replace(/<code\b[\s\S]*?<\/code>/gi, ' ');

for (const file of files) {
  const route = relative(dist, file).replaceAll('\\', '/');
  const html = readFileSync(file, 'utf8');

  // Runs here as well as in check:facts, because the facts check skips without the private
  // CV record, and this repository is public: no page may publish a phone number.
  if (hasPhone(stripHtml(html))) problems.push(`${route}: phone number found`);

  const closings = html.match(/<\/html>/g) ?? [];
  if (closings.length !== 1) {
    problems.push(`${route}: ${closings.length} </html> tags, expected exactly 1`);
  } else {
    const tail = html.slice(html.indexOf('</html>') + '</html>'.length);
    if (tail.trim() !== '') problems.push(`${route}: content after </html>`);
  }

  const ids = [...html.matchAll(/\sid="([^"]*)"/g)].map((match) => match[1]);
  const seen = new Set();
  for (const id of ids) {
    if (seen.has(id)) problems.push(`${route}: duplicate id "${id}"`);
    seen.add(id);
  }

  if (!EXPECTED.has(route) && !isAliasRoute(route) && !isStudyRoute(route)) {
    problems.push(`${route}: unexpected page in dist/`);
    continue;
  }
  if (isAliasRoute(route)) continue;

  const expectedH2 = EXPECTED.has(route) ? EXPECTED.get(route) : null;
  const headings = [...headingSource(html).matchAll(/<(h[1-6])\b[^>]*>([\s\S]*?)<\/\1>/gi)].map((match) => ({
    level: Number(match[1].slice(1)),
    text: plain(match[2]),
  }));

  const h1 = headings.filter((heading) => heading.level === 1);
  if (h1.length !== 1) problems.push(`${route}: ${h1.length} h1 headings, expected exactly 1`);
  if (headings.length === 0) problems.push(`${route}: no headings`);
  else if (headings[0].level !== 1) problems.push(`${route}: first heading is h${headings[0].level}, expected h1`);

  let previous = 0;
  for (const heading of headings) {
    if (previous > 0 && heading.level > previous + 1) {
      problems.push(`${route}: h${heading.level} "${heading.text}" follows h${previous}`);
    }
    previous = heading.level;
  }

  // The page renders copy.json, so this compares the build with the same source: it catches
  // a section that failed to render, not a wording change.
  const h2 = headings.filter((heading) => heading.level === 2).map((heading) => heading.text);
  if (expectedH2 && h2.join(' | ') !== expectedH2.join(' | ')) {
    problems.push(`${route}: h2 sequence is [${h2.join(', ')}], expected [${expectedH2.join(', ')}]`);
  }

}

if (problems.length > 0) {
  console.error('HTML check failed:');
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

console.log(`HTML check OK: ${files.length} pages, one h1 each, heading outlines, unique ids, closed documents.`);
