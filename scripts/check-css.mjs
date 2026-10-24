import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const root = resolve(process.cwd());
const dist = join(root, 'dist');
const problems = [];

if (!existsSync(dist)) {
  console.error('No dist/ directory. Run npm run build first.');
  process.exit(1);
}

const stylesDir = join(dist, '_astro');
if (!existsSync(stylesDir)) {
  console.error('No dist/_astro directory. Run npm run build first.');
  process.exit(1);
}

const cssFiles = readdirSync(stylesDir)
  .filter((name) => name.endsWith('.css'))
  .map((name) => join(stylesDir, name));

if (cssFiles.length === 0) {
  console.error('No CSS bundles found in dist/_astro. Run npm run build first.');
  process.exit(1);
}

const stripComments = (source) => source.replace(/\/\*[\s\S]*?\*\//g, ' ');

// Parses the built bundle so the print rules can be inspected as rules rather than text.
// The bundler already guarantees a valid stylesheet, so this only has to find blocks.
const parseRules = (source, route) => {
  const rules = [];
  const walk = (text, media) => {
    let index = 0;
    while (index < text.length) {
      const brace = text.indexOf('{', index);
      if (brace === -1) break;
      const prelude = text.slice(index, brace).trim();
      let depth = 0;
      let end = -1;
      for (let cursor = brace; cursor < text.length; cursor += 1) {
        if (text[cursor] === '{') depth += 1;
        else if (text[cursor] === '}') {
          depth -= 1;
          if (depth === 0) {
            end = cursor;
            break;
          }
        }
      }
      if (end === -1) throw new Error(`${route}: unterminated block after "${prelude}"`);
      const body = text.slice(brace + 1, end);
      if (prelude.startsWith('@')) walk(body, [...media, prelude]);
      else rules.push({ selector: prelude, body, media, route });
      index = end + 1;
    }
  };
  walk(source, []);
  return rules;
};

const declarations = (body) =>
  body
    .split(';')
    .map((entry) => entry.trim())
    .filter((entry) => entry.includes(':'))
    .map((entry) => {
      const at = entry.indexOf(':');
      return {
        property: entry.slice(0, at).trim().toLowerCase(),
        value: entry
          .slice(at + 1)
          .trim()
          .toLowerCase()
          .replace(/\s+/g, ''),
      };
    });

const selectors = (selector) =>
  selector
    .split(',')
    .map((entry) => entry.trim().replace(/\s+/g, '').replace(/\s*>\s*/g, '>'))
    .filter(Boolean);

const isPrint = (rule) => rule.media.some((media) => /^@media\b[^{]*\bprint\b/.test(media.trim()));

const all = [];
for (const file of cssFiles) {
  const route = relative(dist, file).replaceAll('\\', '/');
  try {
    all.push(...parseRules(stripComments(readFileSync(file, 'utf8')), route));
  } catch (error) {
    problems.push(error.message);
  }
}

if (problems.length > 0) {
  console.error('CSS check failed:');
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

const printRules = all.filter(isPrint);
const declarationsOf = (rule) => declarations(rule.body);

// The header is not in the print probe, so this rule has no other guard.
const headerHidden = printRules.some(
  (rule) =>
    selectors(rule.selector).includes('.site-header') &&
    declarationsOf(rule).some((entry) => entry.property === 'display' && entry.value === 'none!important'),
);
if (!headerHidden) {
  problems.push('missing in the built CSS: a print rule that sets .site-header display:none!important');
}

// A print rule may hide chrome, but never the content a reader came for.
const PRINT_CONTENT = new Set([
  '.project-list',
  '.project-row',
  '.project-cards',
  '.project-card',
  '.card-link',
  '.card-art',
  '.study',
  '.prose',
  '.case-layout',
  '.case-flow',
  '.chips',
]);
for (const rule of printRules) {
  const hides = declarationsOf(rule).some((entry) => entry.property === 'display' && entry.value === 'none');
  if (!hides) continue;
  for (const selector of selectors(rule.selector)) {
    if (PRINT_CONTENT.has(selector)) {
      problems.push(`${rule.route}: a print rule hides ${selector}; the case studies must print`);
    }
  }
}

if (problems.length > 0) {
  console.error('CSS check failed:');
  for (const problem of problems) console.error(`- ${problem}`);
  process.exit(1);
}

console.log(
  `CSS check OK: ${cssFiles.length} bundle, ${printRules.length} print rules, header hidden in print, no print rule hides case-study content.`,
);
