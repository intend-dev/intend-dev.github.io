// This repository is public, so it never stores a phone number, not even as a test
// fixture: a literal would leak through the git history even after it left HEAD.
// Match the shape of any phone number instead. That is stricter than one literal
// and still catches a number the CV or the copy adds later.

const PHONE_SHAPES = [
  /(?:\+|00)\s?\d[\d\s().\-/]{7,}\d/, // an international or trunk prefix, 9-15 digits
  /\b\d{9,}\b/, // one bare national run
  /\b\d{3}[\s.-]\d{3}[\s.-]\d{3}\b/, // three groups of three
];

export const hasPhone = (text) => PHONE_SHAPES.some((pattern) => pattern.test(text));

// The single entity map every HTML-text comparison uses. check-html folds entities in
// headings with it; stripHtml uses it for whole-page text, so a record-vs-page match
// never fails over a typographic apostrophe or middle dot.
export const ENTITIES = new Map([
  ['&amp;', '&'],
  ['&lt;', '<'],
  ['&gt;', '>'],
  ['&quot;', '"'],
  ['&#39;', "'"],
  ['&apos;', "'"],
  ['&nbsp;', ' '],
  ['&rsquo;', '’'],
  ['&lsquo;', '‘'],
  ['&mdash;', '—'],
  ['&middot;', '·'],
]);

const unescapeEntity = (entity) => ENTITIES.get(entity) ?? entity;

export const stripHtml = (html) =>
  html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z#0-9]+;/g, unescapeEntity)
    .replace(/\s+/g, ' ')
    .trim();
