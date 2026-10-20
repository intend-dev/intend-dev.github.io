# Francisco Mateus portfolio

Static portfolio for Francisco Mateus: Application Specialist · AI and Master Data, with Stibo STEP / PIM / MDM work. One overview page, six AI case studies, and the CV as a two-page PDF.

Live URL: <https://intend-dev.github.io/>

## Stack

Astro 7.4.0, TypeScript, plain CSS, `@astrojs/sitemap`. Static output, no client framework, no third-party fonts or scripts. Node 22.

## Commands

```bash
npm install
npm run dev
npm run build
npm run check
npm run test:cv
npm run check:cv-sync
npm run sync:cv
npm run check:links
npm run check:html
npm run check:css
npm run check:seo
npm run check:facts
npm run check:pdf
npm run check:browser  # uv run scripts/check-browser.py
npm run check:all
npm run cv
```

`npm run check:facts` always checks the site's own structure: six case-study files, their frontmatter, their sections, and their titles in the overview. When `../tools-06-cv/record/CAREER.md` is present it also compares published counts, statuses and project values with the record; without the sibling checkout, as in CI, it prints that the record is unverified.

`npm run check:html`, `check:css`, `check:seo`, and `check:pdf` read `dist/` and the committed PDF, so run `npm run build` first; `check:all` does that. They assert one document per page with a single h1 and the expected h2 outline, unique ids, a stylesheet that parses and whose print rules hide the header without hiding case-study content, one sitemap URL with absolute canonicals and robots pointers, and a two-page PDF with an absolute `/#work` link annotation. `check:pdf` reads the PDF with `pypdf` through `uv`, so it needs no Node-side PDF parser.

`npm run check:browser` runs `scripts/check-browser.py` through `uv`, resolving its two PEP 723 dependencies (`websockets`, `pypdf`) on first run, and opens the built page in headless Chromium over CDP and exercises what a static check cannot see: it reloads with scripts disabled to prove the six case studies are present and open natively, emulates print media to assert no doubled rule appears, prints a real A4 PDF to assert it is multi-page, pins an open case study to assert the sticky header's edge appears only while pinned, and switches rows to assert the newly opened study starts under the site header. It needs `dist/` and a Chromium (`CHROME_PATH` overrides the lookup); with no browser it fails, unless `SKIP_BROWSER_CHECK=1` is set deliberately. It also prints `/cv/` and compares the text with the committed PDF, so a stale artifact fails the gate.

`npm run cv` does not build anything; it prints the existing `dist/cv/index.html`, so run `npm run build` first. It writes a temporary file and renames it over `public/Francisco_Mateus_CV.pdf` only after Chromium succeeds, and it listens on an ephemeral port so it never collides with `npm run dev`. It uses a local Chromium to print the unlinked `/cv/` route to `public/Francisco_Mateus_CV.pdf` and fails with a `CHROME_PATH` message when no browser is found. The PDF is committed; CI does not regenerate it.

## Content sources

The career facts come from `../tools-06-cv`:

- `record/CAREER.md` is the authority for roles, dates, numbers, certifications, and education.
- `cv/CV.md` is the CV copy.
- `linkedin/PROFILE.md` is the profile copy.

Update the career record first, then `cv/CV.md`, then run `npm run sync:cv`. That script parses both private files, validates the companies, dates, groups, institutions, project names and numbers against the record, and writes `src/data/career.ts`. Never edit that file by hand. CI has no sibling checkout, so the generated file is committed and `npm run check:cv-sync` skips with a notice instead of regenerating. Do not invent a number, date, title, or outcome.

## Content model

Project case studies are Markdown files under `src/content/projects/`. `src/content.config.ts` validates the frontmatter with Zod: title, summary, order, status, course, year, role, stack, metrics, flow, links, code visibility, and draft. A seventh project is a new Markdown file with the same schema, but `scripts/check-facts.mjs` currently asserts exactly six files with orders 1–6, so that check must be updated in the same change.

The overview inlines all six completed case studies: each row is a `<details>` that expands the full study in place under that row. The six AI projects are private coursework repositories. Their inline bodies state that code is private and document the architecture and evidence. Do not invent repository links. The eight `MS-09-CAP-*` capstones are drafts, so their section is parked: `CapstoneStrip.astro`, `src/data/capstone.ts` and the `.chips` styles stay in the repository, but `index.astro` does not render them. Restore the section when at least one capstone has an artefact, and restore the chip assertions from git history.

## Routing

`site` lives in `astro.config.mjs`; there is no `base`, because the repository is this account's user Pages site (`intend-dev.github.io`) and serves at the origin root. Every internal `href` and `src` goes through `withBase()` from `src/lib/url.ts`; never hard-code `/work/`, `/about/`, `/cv/`, or asset paths. The overview page at `/` holds every section and every case study; the sitemap has that one URL. `/work/` and `/about/` are noindex alias pages that redirect to the overview anchors on `/#work` and `/#about`; `/work/<slug>/` are noindex redirects to `/#case-<slug>`. A custom domain later changes `site` and adds `public/CNAME`.

## robots.txt

`public/robots.txt` deploys to the origin root, where crawlers fetch it, and its `Sitemap:` line points at `sitemap-index.xml` on the same origin.

## Privacy

The public CV PDF and the unlinked print route omit the phone number. The site has no analytics, cookies, third-party scripts, or forms. Keep it that way unless the owner explicitly changes the rule.

## Deployment

Push to `main`. `.github/workflows/deploy.yml` runs `npm run check:all` in a `check` job first, then builds with `withastro/action@v6` and deploys with `actions/deploy-pages@v5`; a failing check blocks the build and the deploy. In the repository settings, Pages → Build and deployment → Source must be `GitHub Actions`.

The build is static and base-path aware. Verify `dist/sitemap-0.xml` uses `https://intend-dev.github.io/` URLs.
