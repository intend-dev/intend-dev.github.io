// Parse the two private sources into the typed data the site renders.
//
// The grammar is deliberately strict: CV.md is the human-edited copy, and a block
// that stops matching its section shape is a source error, not something to guess around.

import { hasPhone } from './facts.mjs';

export class CvSourceError extends Error {}

const fail = (message) => {
  throw new CvSourceError(message);
};

const clean = (text) => text.replace(/\s+/g, ' ').trim();
const untick = (text) => text.replace(/`/g, '').trim();

const sectionsOf = (markdown) => {
  const chunks = markdown.replace(/\r\n/g, '\n').split(/^## /m).slice(1);
  const sections = new Map();
  for (const chunk of chunks) {
    const newline = chunk.indexOf('\n');
    if (newline === -1) continue;
    sections.set(chunk.slice(0, newline).trim(), chunk.slice(newline + 1));
  }
  return sections;
};

const blocksOf = (body) => [...body.matchAll(/```(?:text)?\n([\s\S]*?)```/g)].map((match) => match[1].trim());

const bulletsOf = (lines) => {
  const bullets = [];
  for (const line of lines) {
    if (line.startsWith('- ')) bullets.push(line.slice(2).trim());
    else if (bullets.length > 0 && line.trim() !== '') bullets[bullets.length - 1] += ` ${line.trim()}`;
  }
  return bullets.map(clean);
};

const parseRole = (block, index) => {
  const lines = block.split('\n');
  const role = lines[0]?.trim();
  const head = lines[1]?.trim();
  if (!role || !head) fail(`experience block ${index + 1} needs a role line and a "Company | Period" line`);

  const separator = head.lastIndexOf(' | ');
  if (separator === -1) fail(`experience block ${index + 1}: "${head}" has no "Company | Period" separator`);

  const bullets = bulletsOf(lines.slice(2));
  if (bullets.length === 0) fail(`experience block ${index + 1} (${role}) has no bullets`);

  return { role, left: head.slice(0, separator).trim(), period: head.slice(separator + 3).trim(), bullets };
};

const parseProject = (block, index) => {
  const lines = block.split('\n');
  const name = lines[0]?.trim();
  if (!name) fail(`project block ${index + 1} has no name`);

  const bulletStart = lines.findIndex((line) => line.startsWith('- '));
  const summaryLines = lines.slice(1, bulletStart === -1 ? undefined : bulletStart);
  const summary = clean(summaryLines.join(' '));
  if (!summary) fail(`project block ${index + 1} (${name}) has no summary`);

  const bullets = bulletStart === -1 ? [] : bulletsOf(lines.slice(bulletStart));
  if (bullets.length === 0) fail(`project block ${index + 1} (${name}) has no bullets`);

  return { name, summary, bullets };
};

const parseSkills = (block) => {
  const groups = block
    .split(/\n\s*\n/)
    .map((group) => group.trim())
    .filter(Boolean)
    .map((group) => {
      const lines = group.split('\n');
      const first = lines[0];
      const colon = first.indexOf(': ');
      if (colon === -1) fail(`skills group "${first}" has no "Name: skills" shape`);
      const skills = clean([first.slice(colon + 2), ...lines.slice(1)].join(' '));
      if (!skills) fail(`skills group "${first.slice(0, colon)}" is empty`);
      return { name: first.slice(0, colon).trim(), skills };
    });

  if (groups.length === 0) fail('the skills section has no groups');
  return groups;
};

const parseEducation = (block) => {
  const entries = block
    .split(/\n\s*\n/)
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry, index) => {
      const lines = entry.split('\n').map((line) => line.trim());
      if (lines.length < 2) fail(`education entry ${index + 1} needs an institution and a credential`);
      const detail = clean(lines.slice(2).join(' '));
      return {
        institution: lines[0],
        credential: lines[1],
        ...(detail ? { detail } : {}),
      };
    });

  if (entries.length === 0) fail('the education section has no entries');
  return entries;
};

export const parseCv = (markdown) => {
  const sections = sectionsOf(markdown);
  const required = ['Summary', 'Experience', 'Selected case studies', 'Skills', 'Education'];
  for (const name of required) {
    if (!sections.has(name)) fail(`CV.md has no "## ${name}" section`);
  }

  const summaryBlock = blocksOf(sections.get('Summary'))[0];
  if (!summaryBlock) fail('the summary section has no fenced block');

  const experience = blocksOf(sections.get('Experience')).map(parseRole);
  if (experience.length === 0) fail('the experience section has no blocks');

  const cvProjects = blocksOf(sections.get('Selected case studies')).map(parseProject);
  if (cvProjects.length === 0) fail('the selected case studies section has no blocks');

  return {
    cvSummary: clean(summaryBlock),
    experience,
    cvProjects,
    skillGroups: parseSkills(blocksOf(sections.get('Skills'))[0] ?? ''),
    education: parseEducation(blocksOf(sections.get('Education'))[0] ?? ''),
  };
};

const tableRows = (text) =>
  text
    .split('\n')
    .filter((line) => line.trim().startsWith('|'))
    .map((line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim()))
    .filter((row) => row.length > 1 && row[0] !== 'Certificate' && !/^-+$/.test(row[0]));

const groupBetween = (text, start, end) => {
  const from = text.indexOf(start);
  if (from === -1) fail(`CAREER.md has no "${start}" block`);
  const to = end ? text.indexOf(end, from + start.length) : text.length;
  if (to === -1) fail(`CAREER.md has no "${end}" after "${start}"`);
  return text.slice(from, to);
};

// The four certification groups of the record's Certifications section, as raw table
// rows. Shared by the generator (it names issuers and dates) and the facts check (it
// counts the groups), so one copy parses and the section headers live in one place.
export const certGroupsOf = (markdown) => {
  const section = groupBetween(markdown, '## Certifications', '## Skills ledger');
  const group = (start, end) => tableRows(groupBetween(section, start, end));
  return {
    udacity: group('Udacity Institute of AI & Technology:', 'Stibo Systems:'),
    stibo: group('Stibo Systems:', 'ServiceNow ('),
    serviceNow: group('ServiceNow (', 'Other:'),
    other: group('Other:', null),
  };
};

// One walk of the project inventory table, shared by both consumers: the generator
// projects the verified-number columns from it, the facts check the backticked tokens.
// The capstone subsection rows carry codes the P[1-6] filters drop in both consumers.
export const inventoryRowsOf = (markdown) =>
  tableRows(groupBetween(markdown, '## Project inventory', '### Testing'));

// The per-project verified numbers the record backticks in its inventory. Consumed by
// the facts check, which requires each published study page to carry every token.
export const projectTokensOf = (markdown) => {
  const tokens = new Map();
  for (const row of inventoryRowsOf(markdown)) {
    if (!/^P[1-6]$/.test(row[0]) || row.length < 4) continue;
    const order = Number(row[0].slice(1));
    const found = [...row[3].matchAll(/`([^`]+)`/g)]
      .map((match) => match[1])
      .filter((token) => !token.includes('_') && !token.includes('/') && !/^\d$/.test(token));
    tokens.set(order, found);
  }
  return tokens;
};

export const parseRecord = (markdown) => {
  const locations = [...markdown.matchAll(/^\| (?:Work|Home) location \| ([^|]+) \|/gm)].map((match) =>
    match[1].trim(),
  );

  const groups = certGroupsOf(markdown);
  const certifications = [
    ...groups.udacity.map((row) => ({
      name: untick(row[0]),
      issuer: 'Udacity Institute of AI & Technology',
      date: untick(row[1] ?? ''),
    })),
    ...groups.stibo.map((row) => ({
      name: untick(row[0]),
      issuer: 'Stibo Systems',
      date: untick(row[1] ?? ''),
    })),
    ...groups.serviceNow.map((row) => ({
      name: untick(row[0]),
      issuer: 'ServiceNow',
      date: untick(row[1] ?? ''),
      status: untick(row[2] ?? 'expired'),
    })),
    ...groups.other
      .filter((row) => untick(row[0]) === 'ITIL v4')
      .map((row) => ({
        name: 'ITIL v4',
        issuer: untick(row[1] ?? ''),
        date: untick(row[2] ?? ''),
      })),
  ];

  if (certifications.length === 0) fail('CAREER.md produced no certifications');
  const names = certifications.map((certification) => certification.name);
  if (new Set(names).size !== names.length) fail('CAREER.md has duplicate certificate names');

  const careerText = groupBetween(markdown, '## Career record', '## Certifications');
  const projectNumbers = new Map(
    inventoryRowsOf(markdown)
      .filter((row) => /^P[1-6]$/.test(row[0]))
      .map((row) => [row[1], row.slice(3).join(' ')]),
  );

  return { markdown, careerText, locations, certifications, projectNumbers };
};

const splitCompany = (left, locations) => {
  for (const location of [...locations].sort((a, b) => b.length - a.length)) {
    const suffix = `, ${location}`;
    if (left.endsWith(suffix)) return { company: left.slice(0, -suffix.length).trim(), location };
  }
  return { company: left, location: '' };
};

const NUMBER = /\d[\d,]*(?:\.\d+)?/g;

export const buildCareerData = (cv, record) => {
  const experience = cv.experience.map((role) => {
    const { company, location } = splitCompany(role.left, record.locations);
    const companyKey = company.split(/\s+/)[0];
    if (!record.careerText.includes(companyKey)) fail(`CV.md company "${company}" is not in the career record`);
    for (const part of role.period.split(/\s+to\s+/)) {
      if (!record.careerText.includes(part)) fail(`CV.md period "${part}" (${company}) is not in the career record`);
    }
    return {
      company,
      role: role.role,
      ...(location ? { location } : {}),
      period: role.period,
      bullets: role.bullets,
    };
  });

  for (const group of cv.skillGroups) {
    if (!record.markdown.includes(group.name)) fail(`skill group "${group.name}" is not in CAREER.md`);
  }

  for (const entry of cv.education) {
    if (!record.markdown.includes(entry.institution)) {
      fail(`education institution "${entry.institution}" is not in CAREER.md`);
    }
  }

  for (const project of cv.cvProjects) {
    const row = record.projectNumbers.get(project.name);
    if (!row) fail(`project "${project.name}" is not in the project inventory`);
    const numbers = [...`${project.summary} ${project.bullets.join(' ')}`.matchAll(NUMBER)].map((match) =>
      match[0].replace(/,$/, ''),
    );
    for (const number of numbers) {
      if (!row.includes(number)) fail(`project "${project.name}" number ${number} is not in its inventory row`);
    }
  }

  const data = {
    experience,
    skillGroups: cv.skillGroups,
    certifications: record.certifications,
    education: cv.education,
    cvSummary: cv.cvSummary,
    cvProjects: cv.cvProjects,
  };

  if (hasPhone(JSON.stringify(data))) fail('the generated data contains a phone-shaped string');
  return data;
};

export const serializeCareer = (data) => {
  const block = (name, value) => `export const ${name} = ${JSON.stringify(value, null, 2)} as const;\n`;
  return `// Generated by scripts/sync-cv.mjs from ../tools-06-cv/cv/CV.md and
// ../tools-06-cv/record/CAREER.md. Do not edit; run \`npm run sync:cv\`.

${block('experience', data.experience)}
${block('skillGroups', data.skillGroups)}
${block('certifications', data.certifications)}
${block('education', data.education)}
export const cvSummary = ${JSON.stringify(data.cvSummary)};

${block('cvProjects', data.cvProjects)}`;
};
