# Jian Kuang's Blog

Personal blog and portfolio built with Astro.

## Tech Stack

- [Astro 5](https://astro.build) - Static site generator
- [Tailwind CSS v4](https://tailwindcss.com) - Styling
- [MDX](https://mdxjs.com) - Blog content

## Development

```bash
npm install
npm run dev     # Start dev server at localhost:4321
npm run build   # Build static site to dist/
npm run preview # Preview built site
```

## Structure

```
src/
├── content/blog/     # MDX blog posts
├── components/       # Astro components
├── layouts/          # Base layout
├── pages/            # Routes (/, /about, /projects, /blog/[slug])
└── styles/           # Global CSS
```
