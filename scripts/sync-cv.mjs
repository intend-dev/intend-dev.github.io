// Generate src/data/career.ts from the private record and CV copy.
//
// The output is committed because CI checks out only this repository. `--check`
// regenerates in memory and compares; it skips, with a notice, when the sibling
// checkout is absent (CI).

import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { CvSourceError, buildCareerData, parseCv, parseRecord, serializeCareer } from './lib/cv-source.mjs';

const root = resolve(process.cwd());
const cvPath = join(root, '..', 'tools-06-cv', 'cv', 'CV.md');
const recordPath = join(root, '..', 'tools-06-cv', 'record', 'CAREER.md');
const outPath = join(root, 'src', 'data', 'career.ts');
const check = process.argv.includes('--check');

if (!existsSync(cvPath) || !existsSync(recordPath)) {
  if (check) {
    console.log('CV sync check skipped: ../tools-06-cv is not present.');
    process.exit(0);
  }
  console.error(`CV sync needs ${cvPath} and ${recordPath}.`);
  process.exit(1);
}

try {
  const data = buildCareerData(
    parseCv(readFileSync(cvPath, 'utf8')),
    parseRecord(readFileSync(recordPath, 'utf8')),
  );
  const output = serializeCareer(data);

  if (check) {
    const current = existsSync(outPath) ? readFileSync(outPath, 'utf8') : '';
    if (current !== output) {
      console.error('src/data/career.ts is stale. Run `npm run sync:cv`.');
      process.exit(1);
    }
    console.log('CV sync check OK: career.ts matches CV.md and CAREER.md.');
  } else {
    writeFileSync(outPath, output);
    console.log(
      `CV sync OK: ${data.experience.length} roles, ${data.skillGroups.length} skill groups, ` +
        `${data.certifications.length} certifications, ${data.education.length} education entries, ` +
        `${data.cvProjects.length} projects.`,
    );
  }
} catch (error) {
  if (error instanceof CvSourceError) {
    console.error(`CV sync failed: ${error.message}`);
    process.exit(1);
  }
  throw error;
}
