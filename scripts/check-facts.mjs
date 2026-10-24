import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { stripHtml } from './lib/facts.mjs';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const recordPath = join(root, '..', 'tools-06-cv', 'record', 'CAREER.md');
const problems = [];

const requireText = (file, label) => {
  if (!existsSync(file)) {
    problems.push(`${label} is missing: ${file}`);
    return '';
  }
  return readFileSync(file, 'utf8');
};

if (!existsSync(dist)) {
  console.error('No dist/ directory. Run npm run build first.');
  process.exit(1);
}

const readRaw = (relative) => requireText(join(dist, relative), relative);
const readPage = (relative) => stripHtml(readRaw(relative));
const homeHtml = readRaw('index.html');
const workHtml = readRaw('work/index.html');
const home = stripHtml(homeHtml);
const work = stripHtml(workHtml);
const cv = readPage('cv/index.html');

// The profile shows its featured studies as cards; the work page is the index of six.
// Counting them is the outcome: a fourth card on the profile, or a missing row on the
// index, is a defect a visitor sees.
const cardCount = (html) => (html.match(/class="project-card"/g) ?? []).length;
const rowCount = (html) => (html.match(/class="project-row"/g) ?? []).length;
if (cardCount(homeHtml) !== 3) {
  problems.push(`the profile shows ${cardCount(homeHtml)} study cards, expected 3`);
}
if (rowCount(workHtml) !== 6) {
  problems.push(`the work page shows ${rowCount(workHtml)} study rows, expected all 6`);
}

// ---------------------------------------------------------------------------
// Structure: runs everywhere, including CI, and needs nothing outside this repo.
// ---------------------------------------------------------------------------

// Only strings that would break a rule if they came back: a wrong certification status,
// a wrong count or title, a disclaimer we should never need, an unsupported precision.
// Earlier drafts of the copy are not a rule; banning them tests history, not the site.
const banned = [
  'ServiceNow active',
  're-certified',
  '9 Udacity AI',
  'AI Engineer · Master Data Specialist',
  'No phone number is published',
  '98.83%',
];
const publishedText = [home, cv].join(' ');
for (const needle of banned) {
  if (publishedText.includes(needle)) problems.push(`Stale published string: ${needle}`);
}

const projectDir = join(root, 'src', 'content', 'projects');
const projectFiles = existsSync(projectDir)
  ? readdirSync(projectDir).filter((file) => file.endsWith('.md'))
  : [];
// Six studies is a content decision; a seventh needs this number and its checks updated.
if (projectFiles.length !== 6) problems.push(`Expected 6 case studies, found ${projectFiles.length}.`);

const orders = new Set();
let featuredCount = 0;
for (const file of projectFiles) {
  const source = readFileSync(join(projectDir, file), 'utf8');
  const frontmatter = source.slice(0, source.indexOf('---', 3));
  const title = frontmatter.match(/^title:\s*"(.+)"$/m)?.[1];
  const courseProject = frontmatter.match(/^courseProject:\s*"(.+)"$/m)?.[1];
  const order = Number(frontmatter.match(/^order:\s*(\d+)$/m)?.[1]);
  const featured = /^featured:\s*true$/m.test(frontmatter);
  const slug = file.replace(/\.md$/, '');
  const studyPage = join(dist, 'work', slug, 'index.html');
  if (featured) featuredCount += 1;

  if (!title) problems.push(`${file}: missing title`);
  if (!courseProject) problems.push(`${file}: missing courseProject`);
  if (!Number.isInteger(order) || order < 1 || order > 6) problems.push(`${file}: invalid order`);
  if (orders.has(order)) problems.push(`${file}: duplicate order ${order}`);
  orders.add(order);
  if (!/^status:\s*completed$/m.test(frontmatter)) problems.push(`${file}: status is not completed`);
  if (!/^code:\s*private$/m.test(frontmatter)) problems.push(`${file}: code is not private`);
  if (!/^year:\s*"2026"$/m.test(frontmatter)) problems.push(`${file}: year is not 2026`);
  if (!/^role:\s*"Solo build"$/m.test(frontmatter)) problems.push(`${file}: role is not Solo build`);

  for (const section of ['Context', 'Constraint', 'What I built', 'Evidence', 'What broke', "What I'd do next"]) {
    if (!source.includes(`## ${section}`)) problems.push(`${file}: missing section ${section}`);
  }

  if (title && !work.includes(title)) problems.push(`${file}: title missing from the work page`);
  if (!existsSync(studyPage)) {
    problems.push(`${file}: missing study page dist/work/${slug}/index.html`);
  } else {
    const studyText = stripHtml(readFileSync(studyPage, 'utf8'));
    if (title && !studyText.includes(title)) problems.push(`${file}: title missing from its study page`);
  }
  if (title && featured && !home.includes(title)) problems.push(`${file}: featured title missing from the profile`);
}

// Three featured studies is a content decision, like the six; a fourth card needs this
// number and the profile layout revisited.
if (featuredCount !== 3) problems.push(`Expected 3 featured studies, found ${featuredCount}.`);

if (problems.length > 0) {
  console.error(`Facts check failed with ${problems.length} problem(s):`);
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

if (!existsSync(recordPath)) {
  console.log(
    'Facts check OK (structure only): 6 case studies and their metadata; the career record is not present, so counts, dates and numbers are unverified.',
  );
  process.exit(0);
}

// ---------------------------------------------------------------------------
// Record: needs the private career record. CI has no such checkout.
// ---------------------------------------------------------------------------

const record = requireText(recordPath, 'Career record');
const certSection = record.slice(
  record.indexOf('## Certifications (full set'),
  record.indexOf('## Skills ledger'),
);

const tableRows = (text) =>
  text
    .split('\n')
    .filter((line) => line.trim().startsWith('|'))
    .map((line) =>
      line
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((cell) => cell.trim()),
    )
    .filter((row) => row.length > 1 && row[0] !== 'Certificate' && !row[0].startsWith('---'));

const certGroup = (start, end) => {
  const from = certSection.indexOf(start);
  const to = end ? certSection.indexOf(end, from + start.length) : certSection.length;
  return from === -1 ? [] : tableRows(certSection.slice(from, to));
};

const udacity = certGroup('Udacity Institute of AI & Technology:', 'Stibo Systems:');
const stibo = certGroup('Stibo Systems:', 'ServiceNow (');
const serviceNow = certGroup('ServiceNow (', 'Other:');
const other = certGroup('Other:', null);

const projectSection = record.slice(
  record.indexOf('## Project inventory'),
  record.indexOf('### Testing'),
);
const projectTokens = new Map();
for (const row of tableRows(projectSection)) {
  if (!/^P[1-6]$/.test(row[0]) || row.length < 4) continue;
  const order = Number(row[0].slice(1));
  const tokens = [...row[3].matchAll(/`([^`]+)`/g)]
    .map((match) => match[1])
    .filter((token) => !token.includes('_') && !token.includes('/') && !/^\d$/.test(token));
  projectTokens.set(order, tokens);
}

// The page must publish what the record says. The checks below derive the expected text
// from the record itself, so a record change surfaces here and not in a duplicated literal.
const required = [
  [`${udacity.length} Udacity AI`, home],
  [`${stibo.length} Stibo Systems`, home],
  [`${serviceNow.length} ServiceNow expired`, home],
];
for (const [needle, haystack] of required) {
  if (!haystack.includes(needle)) problems.push(`Missing published fact: ${needle}`);
}

for (const file of projectFiles) {
  const source = readFileSync(join(projectDir, file), 'utf8');
  const frontmatter = source.slice(0, source.indexOf('---', 3));
  const courseProject = frontmatter.match(/^courseProject:\s*"(.+)"$/m)?.[1];
  const order = Number(frontmatter.match(/^order:\s*(\d+)$/m)?.[1]);
  const studyText = readPage(`work/${file.replace(/\.md$/, '')}/index.html`);
  if (courseProject && !record.includes(courseProject)) {
    problems.push(`${file}: courseProject not found in the career record`);
  }
  for (const token of projectTokens.get(order) ?? []) {
    if (!studyText.includes(token)) problems.push(`${file}: record value ${token} missing from its study page`);
  }
}

if (problems.length > 0) {
  console.error(`Facts check failed with ${problems.length} problem(s):`);
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

console.log(
  `Facts check OK (structure and record): ${udacity.length} Udacity, ${stibo.length} Stibo, ${serviceNow.length} expired ServiceNow, ${other.length} other; ${projectFiles.length} case studies.`,
);
