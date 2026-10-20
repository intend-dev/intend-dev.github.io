import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://intend-dev.github.io',
  trailingSlash: 'always',
  output: 'static',
  integrations: [
    sitemap({
      filter: (page) =>
        !page.endsWith('/404/') &&
        !page.endsWith('/404') &&
        !page.endsWith('/cv/') &&
        !page.endsWith('/cv') &&
        !page.includes('/work/') &&
        !page.endsWith('/work') &&
        !page.endsWith('/about/') &&
        !page.endsWith('/about'),
    }),
  ],
});
