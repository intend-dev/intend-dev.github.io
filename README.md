# Francisco Mateus: Portfolio

Personal portfolio and project case studies for an Application Specialist working across AI, master data, and Stibo STEP / PIM / MDM.

**Live site:** https://intend-dev.github.io/

The site includes a profile page, a case-study index at `/work/`, individual pages for each case study, and a two-page CV PDF. It is a static Astro site designed to render without client-side JavaScript.

## At a glance

- **Framework:** Astro 7.4.0 and TypeScript
- **Styling:** Plain CSS
- **Output:** Static HTML
- **Integrations:** `@astrojs/sitemap`
- **Runtime:** Node.js 22
- **Browser testing:** Headless Chromium, driven through CDP
- **Privacy:** No analytics, cookies, forms, third-party fonts, or third-party scripts

## Getting started

Install dependencies and start the development server:

```bash
npm install
npm run dev
```

Create a production build with:

```bash
npm run build
```

## Available commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Start the local development server |
| `npm run build` | Build the static site into `dist/` |
| `npm run check` | Run the project's standard Astro checks |
| `npm run test:cv` | Test CV-related logic |
| `npm run check:cv-sync` | Check generated career data against the source record when available |
| `npm run sync:cv` | Regenerate `src/data/career.ts` from the career sources |
| `npm run check:links` | Check links |
| `npm run check:html` | Validate generated HTML structure |
| `npm run check:css` | Validate CSS and print styles |
| `npm run check:seo` | Validate SEO metadata and sitemap output |
| `npm run check:facts` | Check portfolio content and expected project facts |
| `npm run check:pdf` | Validate the committed CV PDF |
| `npm run check:browser` | Test rendered pages and browser behaviour in Chromium |
| `npm run check:all` | Run the full validation workflow |
| `npm run cv` | Regenerate the committed CV PDF from the built print route |

Run `npm run check:all` before submitting changes. It builds the site before checks that depend on `dist/`.

### What the checks cover

- **Content and structure:** six case-study files, valid frontmatter and sections, exactly three featured studies, the profile cards, and all six entries on the work index.
- **HTML, CSS, and SEO:** document outlines, one `h1` per page, unique IDs, valid CSS, print styles, absolute canonical URLs, sitemap entries, and robots pointers.
- **CV PDF:** confirms the committed PDF has two pages and contains the expected work-page link annotation. PDF inspection uses `pypdf` through `uv`, without a Node-side PDF parser.
- **Browser behaviour:** opens built pages in headless Chromium over CDP; checks that content and links work with JavaScript disabled; exercises print media, sticky headers, alignment, transitions, reduced-motion behaviour, card hover, and the absence of third-party scripts and stylesheets. It also prints the CV route and compares its text with the committed PDF.
- **Source-record consistency:** when the sibling `../tools-06-cv/record/CAREER.md` checkout is present, checks published career and project facts against it. In CI, where that private sibling checkout is absent, the check reports that the source record could not be verified.

The HTML, CSS, SEO, and PDF checks inspect the built output, so run `npm run build` first or use `npm run check:all`.

### Browser and PDF prerequisites

The browser check runs `scripts/check-browser.py` through `uv`. Its PEP 723 dependencies (`websockets` and `pypdf`) are resolved on first run. A local Chromium installation is required; set `CHROME_PATH` to its executable if automatic discovery fails.

Without Chromium, the browser check fails by design. Set `SKIP_BROWSER_CHECK=1` only when deliberately opting out.

To regenerate the CV PDF, first run `npm run build`, then `npm run cv`. The command prints the existing, unlinked `/cv/` route to `public/Francisco_Mateus_CV.pdf`, writing to a temporary file and replacing the committed PDF only after Chromium succeeds. It uses an ephemeral local port and does not regenerate the PDF in CI.

## Content and data

### Career information

Career facts are maintained in the sibling `../tools-06-cv` checkout:

- `record/CAREER.md` is the source of truth for roles, dates, figures, certifications, and education.
- `cv/CV.md` contains the CV copy.
- `linkedin/PROFILE.md` contains the profile copy.

Update the career record first, then the CV copy, and run:

```bash
npm run sync:cv
```

The sync script validates companies, dates, groups, institutions, project names, and figures against the source record, then writes `src/data/career.ts`. **Do not edit `src/data/career.ts` by hand.** The generated file is committed because CI does not have the sibling checkout; in that environment, `npm run check:cv-sync` skips regeneration with a notice. Do not invent dates, titles, figures, or outcomes.

### Case studies

Case studies live as Markdown files in `src/content/projects/`. Their frontmatter is validated with Zod in `src/content.config.ts`, including title, summary, order, status, course, year, role, stack, metrics, flow, links, code visibility, draft, and featured status.

Each completed study has its own page at `/work/<slug>/`. The page uses the study title as its `h1`, reuses `CaseStudyBody` for the flow, metrics, and prose, opens with the study diagram, and closes with an “At a glance” strip followed by previous/next navigation. The `/work/` page lists all six studies. The profile shows the three `featured: true` studies as linked cards, each with a hand-drawn SVG diagram, plus a link to the full index.

The six AI projects are private coursework repositories. Their pages describe the architecture and evidence and state that the code is private. Do not add repository links unless they are real and intended to be public.

The eight `MS-09-CAP-*` capstones are drafts, so the capstone section is intentionally not rendered. Related code and styles remain in the repository. Restore the section only when at least one capstone has an artefact, and restore the relevant assertions from Git history at the same time.

**Adding a case study:** create a Markdown file that follows the existing schema. The current `scripts/check-facts.mjs` check expects exactly six files ordered 1–6, so update that check in the same change when adding or removing a study.

## Design and motion

Motion is intentionally restrained and respects reduced-motion preferences. Below-the-fold sections reveal once without transforms that would interfere with sticky headers; SVG diagrams draw on entry only when `prefers-reduced-motion: no-preference`. Without JavaScript or with reduced motion enabled, the completed markup remains visible.

Navigation uses Astro's first-party View Transitions through `ClientRouter` in `BaseLayout`. The header persists between pages, and card artwork shares transition names with its corresponding study page. Inline scripts reinitialise on `astro:page-load` with one-time guards.

## Routing and SEO

The `site` URL is configured in `astro.config.mjs`. There is no `base` path because this repository is the account's GitHub Pages site and is served from the origin root.

Internal links and asset paths should use `withBase()` from `src/lib/url.ts`; avoid hard-coding route or asset paths. The main routes are:

- `/` — profile, featured case studies, capabilities, About, and contact
- `/work/` — case-study index
- `/work/<slug>/` — individual case studies
- `/cv/` — unlinked print route used to generate the CV PDF
- `/about/` — noindex alias that redirects to `/#story`

The sitemap currently lists eight URLs. `public/robots.txt` is served from the origin root and points crawlers to `sitemap-index.xml` on the same origin. After a routing or domain change, verify the URLs in `dist/sitemap-0.xml`.

## Privacy

The public CV PDF and the unlinked print route omit the phone number. The site has no analytics, cookies, third-party scripts, or forms. Preserve these constraints unless the owner explicitly changes them.

## Deployment

Push changes to `main`. The workflow in `.github/workflows/deploy.yml` runs `npm run check:all` before building and deploying the site. A failed check blocks deployment. The workflow uses `withastro/action@v6` and `actions/deploy-pages@v5`.

In repository settings, set **Pages → Build and deployment → Source** to **GitHub Actions**.

## License

All rights reserved. `LICENSE` contains the notice for this repository's own work. `THIRD_PARTY_NOTICES.md` records the licenses of third-party code included in the site output.
