export const capstoneRepoBase = 'https://github.com/intend-dev/';
export const capstoneRepos = [
  'MS-09-CAP-02-AIPF',
  'MS-09-CAP-03-SA',
  'MS-09-CAP-04-AML',
  'MS-09-CAP-05-DLS',
  'MS-09-CAP-06-GAA',
  'MS-09-CAP-07-AW',
  'MS-09-CAP-08-AISS',
  'MS-09-CAP-09-PID',
] as const;

/**
 * Short display names, recorded in `../tools-06-cv/record/CAREER.md` (SRC11).
 * `satisfies` keeps this map in step with the list above: adding a repository
 * without a name is a type error, not an unlabelled chip.
 */
export const capstoneNames = {
  'MS-09-CAP-02-AIPF': 'AI Programming Foundations',
  'MS-09-CAP-03-SA': 'Statistical Analysis in Python',
  'MS-09-CAP-04-AML': 'Applied Machine Learning',
  'MS-09-CAP-05-DLS': 'Deep Learning Systems',
  'MS-09-CAP-06-GAA': 'Generative AI Applications',
  'MS-09-CAP-07-AW': 'Agentic Workflows',
  'MS-09-CAP-08-AISS': 'AI Systems Synthesis',
  'MS-09-CAP-09-PID': 'Industry Defense',
} satisfies Record<(typeof capstoneRepos)[number], string>;
