import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://intend-dev.github.io',
  // 'ignore': the live GitHub Pages host redirects slashless directory URLs to their
  // slash form, and the local preview should accept both too, not 404 on /work.
  trailingSlash: 'ignore',
  output: 'static',
  integrations: [
    sitemap({
      // /work/ is a real page and belongs in the sitemap; the /work/<slug>/ redirects
      // and the other alias pages stay out. The filter receives full URLs.
      filter: (page) =>
        !page.endsWith('/404/') &&
        !page.endsWith('/404') &&
        !page.endsWith('/cv/') &&
        !page.endsWith('/cv') &&
        !(page.includes('/work/') && !page.endsWith('/work/')) &&
        !page.endsWith('/about/') &&
        !page.endsWith('/about'),
    }),
  ],
});
