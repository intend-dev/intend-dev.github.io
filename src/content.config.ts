import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

const projects = defineCollection({
  loader: glob({ base: './src/content/projects', pattern: '**/*.md' }),
  schema: z.object({
    title: z.string(),
    courseProject: z.string(),
    summary: z.string(),
    order: z.number().int(),
    status: z.enum(['completed', 'in-progress', 'legacy']).default('completed'),
    course: z.string(),
    year: z.string(),
    role: z.string(),
    stack: z.array(z.string()).min(1),
    metrics: z
      .array(z.object({ value: z.string(), label: z.string() }))
      .min(2)
      .max(4),
    flow: z
      .array(z.object({ label: z.string(), note: z.string().optional() }))
      .min(3)
      .max(6),
    links: z
      .array(
        z.object({
          label: z.string(),
          href: z.url(),
          kind: z.enum(['demo', 'repo', 'docs']),
        }),
      )
      .default([]),
    code: z.enum(['public', 'private']).default('private'),
    draft: z.boolean().default(false),
    // Featured studies render as cards on the profile; every study gets its own page.
    featured: z.boolean().default(false),
  }),
});

export const collections = { projects };
