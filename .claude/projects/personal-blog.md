---
project: personal-blog
created: 2025-12-26
last_updated: 2025-12-26
---
# Project: Personal Blog / Portfolio

## Location

/Volumes/Projects/Dev/Repos/jian

## Repository

https://github.com/jkuang7/jian.git

## Current Goal

Build a personal portfolio/blog website that signals senior engineering competence to startup interviewers. The site should convey: "I learn fast, I move fast, I'm honest about failures, and I take ownership."

## Context

### Target Audience

Startup hiring managers and interviewers scanning portfolios quickly.

### Core Signal

Not cold metrics or generic claims. Instead: **authenticity + growth mindset + execution**.

Key qualities to convey:

- Fast learner who figures things out under ambiguity
- Delivers quickly without getting stuck
- Honest about mistakes, learns from them
- Takes ownership and leadership

### Technical Stack

| Component | Choice                  | Rationale                 |
| --------- | ----------------------- | ------------------------- |
| Framework | Next.js 15 + TypeScript | Industry standard         |
| Styling   | Tailwind CSS            | Fast, good dark mode      |
| Content   | MDX + gray-matter       | Local files, AI-friendly  |
| Syntax    | Shiki                   | Fast, no runtime JS       |
| Hosting   | Cloudflare Pages        | Free, unlimited bandwidth |
| Domain    | jian.pages.dev          | Free subdomain            |

### Constraints

- $0 budget (completely free tier)
- No backend needed
- Images stored in git repo (<500KB each, WebP)
- Content updates infrequently

---

## Site Structure

```
/                      # Home: hook + links + latest writing + company logos
/blog                  # Blog index with tag filtering
/blog/[slug]           # Individual blog posts (MDX)
/projects              # Project grid (starts empty, grows over time)
/about                 # Bio/intro page
/career/attentive      # Attentive experience detail
/career/statefarm      # State Farm experience detail
```

**7 routes total.**

### Navigation

- Home | Blog | Projects | About | Career (dropdown)
- "Career" is dropdown-only (no landing page)
- Dropdown shows: Attentive, State Farm (with hover highlight)

---

## Homepage Design

**Approach**: Authentic hook + career pages as main proof + blog posts + company logos for credibility.

```
┌─────────────────────────────────────┐
│  Jian Kuang                         │
│  [Authentic 1-line hook]            │
│                                     │
│  → Work                             │
│  → Writing                          │
│  → About                            │
│                                     │
│  ─────────────────────────────      │
│  Latest Writing                     │
│  • Post title →                     │
│  • Post title →                     │
│                                     │
│  ─────────────────────────────      │
│  Previously at                      │
│  Attentive • State Farm             │
└─────────────────────────────────────┘
```

**Hook options** (to be finalized):

- "I break things, fix them, and learn fast enough to not break them again."
- "I figure things out. Fast."
- "I've shipped code that broke production and learned how to prevent it"
- Custom line that feels authentic to you

**When projects exist**: They slot in between links and latest writing.

---

## Career Pages Content Structure

Each `/career/[company]` page should contain:

1. **Context** - What situation you walked into
2. **What you did** - Specific, credible detail
3. **What you learned** - Honest reflection
4. **Outcome** - Doesn't need numbers ("shipped it" counts)

These pages are your **main proof** of the qualities you want to signal.

---

## UX Philosophy

### Do

- Instant page loads (<1s)
- Dark mode without flash
- Clean typography, generous whitespace
- Subtle micro-interactions on hover
- Mobile-first responsive
- Working RSS feed
- Proper meta tags/OG images

### Skip

- 3D animations, particle effects
- Page transition animations
- Comment system
- Newsletter signup
- Analytics dashboard
- Fancy cursor effects

---

## Content Files (AI-Friendly Editing)

| File                         | Purpose                              |
| ---------------------------- | ------------------------------------ |
| `content/blog/*.mdx`       | Blog posts                           |
| `content/data/projects.ts` | Project list (starts empty)          |
| `content/data/career.ts`   | Career experiences                   |
| `content/data/site.ts`     | Site config (name, links, bio, hook) |

### Workflow

- `npm run new-post "Title"` scaffolds new posts
- `git push` → auto-deploys to Cloudflare

---

## Scope

**In scope:**

- 7 routes (home, blog, blog/[slug], projects, about, career/attentive, career/statefarm)
- Nav dropdown for career section with hover highlight
- Dark mode (system default + toggle + persist)
- MDX blog with syntax highlighting
- RSS feed
- Responsive design
- Lighthouse 95+

**NOT in scope:**

- Project detail pages (`/projects/[slug]`)
- Comments
- Newsletter
- Analytics
- Custom domain
- Animations beyond subtle hover effects

---

## Acceptance Criteria

- [ ] Site deploys to `jian.pages.dev`
- [ ] All 7 routes render correctly
- [ ] Nav dropdown works with hover highlight
- [ ] Dark mode works without flash
- [ ] `npm run new-post` scaffolds blog posts
- [ ] Lighthouse performance ≥ 95
- [ ] RSS feed validates

---

## Risks Identified

1. **Content data layer structure** (Step 4): Dependency risk
   → Mitigation: Design schema that serves all consumers (homepage, blog, career, projects) before implementing

2. **MDX + Shiki + Next.js 15** (Step 6): Integration risk
   → Mitigation: Use @next/mdx with app router pattern; Shiki via rehype-pretty-code

3. **OG image generation** (Step 14): Complexity risk
   → Mitigation: Use Next.js ImageResponse API (not external services)

4. **Cloudflare Pages build** (Step 15): Integration risk
   → Mitigation: Use @cloudflare/next-on-pages or static export; verify build locally first

---

## Steps

- [x] Step 1: Project scaffolding (AUTO) - DONE
- [x] Step 2: Dark mode system (AUTO) - DONE
- [ ] Step 3: Navigation with career dropdown (AUTO)

### Step 4: Content data layer (AUTO)
**Outcome**: Typed data files exist that all pages can import
**Context**:
- Design schema to serve: homepage (hook, links), blog (posts list), career (experiences), projects (grid)
- Use `content/data/*.ts` with TypeScript types exported
- Keep flat structure, avoid over-abstraction
**NOT in scope**: Actual page implementations

- [ ] Step 5: Homepage (CHECKPOINT)

### Step 6: MDX blog infrastructure (AUTO)
**Outcome**: MDX files in `content/blog/` render as pages with syntax highlighting
**Context**:
- Use `@next/mdx` with app router (`mdx-components.tsx`)
- Syntax highlighting via `rehype-pretty-code` + Shiki (no runtime JS)
- Frontmatter via `gray-matter` in a loader or generateStaticParams
**NOT in scope**: Blog index page, styling

- [ ] Step 7: Blog index page (AUTO)
- [ ] Step 8: Blog post page (CHECKPOINT)
- [ ] Step 9: About page (AUTO)
- [ ] Step 10: Career detail pages (AUTO)
- [ ] Step 11: Projects page (CHECKPOINT)
- [ ] Step 12: RSS feed (AUTO)
- [ ] Step 13: new-post script (AUTO)

### Step 14: Meta tags & OG images (AUTO)
**Outcome**: Pages have proper meta tags; OG images generate dynamically
**Context**:
- Use Next.js Metadata API in layout/page files
- OG images via `ImageResponse` from `next/og` (edge runtime)
- Template: name + page title on simple background
**NOT in scope**: Custom per-post OG images

### Step 15: Cloudflare deployment (CHECKPOINT)
**Outcome**: Site live at jian.pages.dev
**Context**:
- Option A: Static export (`output: 'export'`) — simpler, no edge functions
- Option B: `@cloudflare/next-on-pages` — if dynamic features needed
- Test build locally with `npx wrangler pages dev` before pushing
**NOT in scope**: Custom domain setup

---

## History

- 2025-12-26: Plan created
