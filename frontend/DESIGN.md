# Remote Impact design system

shadcn/ui-style tokens and components, implemented as Tailwind 3 utilities and
`@layer components` classes (see `ui.css`, `tailwind.config.js`). Django
templates use these class names directly; there is no React.

Build after changing templates (the CSS only contains classes it finds):

    cd frontend && npm install && npm run build   # writes ../static/tailwind.min.css

## Principles

- Calm, editorial, trustworthy. Warm off-white page, white cards, ink text,
  ink actions, and colour reserved for meaningful states and impact areas.
- One primary action per view. Everything else is outline/ghost/link.
- Mobile first. Every page must work at 360px wide with no horizontal scroll.
- Content width: `container` (max 1200px) or `page-narrow` (max 768px) for forms/reading.
- Accessible: contrast >= 4.5:1 for text, visible focus (`:focus-visible` ring is global),
  real `<button>`/`<a>` elements, labels on inputs, `aria-label` on icon-only buttons.

## Tokens (CSS variables, use via Tailwind names)

| Token | Use |
|---|---|
| `bg-background` / `text-foreground` | page background, body text |
| `bg-card` / `text-card-foreground` | cards, panels, inputs |
| `bg-muted` / `text-muted-foreground` | subtle fills, secondary text |
| `bg-primary` / `text-primary-foreground` | ink primary buttons (white text) |
| `bg-secondary` / `text-secondary-foreground` | muted secondary controls (ink text) |
| `bg-accent` / `text-accent-foreground` | neutral hover and selected fills |
| `border-border`, `border-input`, `ring-ring` | borders, inputs, focus |
| `bg-destructive`, `success`, `warning` | states |
| `brand-50..900` | legacy brand scale, still valid |
| radius: `rounded-lg` (12px), `rounded-md`, `rounded-sm`; shadows: `shadow-card`, `shadow-lift` |

Fonts: locally hosted variable Geist for headings and body, Geist Mono for code.
`font-display` and `font-sans` share the same family. No external font stylesheet.

## Components

- Layout: `page`, `page-narrow`, `container`, `section-title`, `page-title`, `page-lede`, `kicker`, `muted`, `separator`
- Button: `btn` + `btn-primary | btn-secondary | btn-outline | btn-ghost | btn-link | btn-destructive`, sizes `btn-sm | btn-lg`, `btn-block`
- Card: `card`, `card-header`, `card-title`, `card-description`, `card-content`, `card-footer`, `card-interactive`
- Badge: `badge` + `badge-default | badge-primary | badge-outline | badge-featured | badge-success | badge-warning`
- Forms: `label`, `input`, `select`, `textarea`, `help`, `checkbox`
- Alert: `alert` + `alert-info | alert-success | alert-warning | alert-destructive`
- Stat: `stat`, `stat-value`, `stat-label`
- Table: `table-wrap` > `table`
- Tabs: `tabs` > `tab` / `tab-active`
- Jobs: `job-row` (clickable card), `job-title`, `job-meta`
- Lists: `check-list` (✓ bullets)

## Legacy rules to respect while migrating

`templates/base.html` still contains overrides from an old dark theme
(`.text-white` renders dark ink, `.bg-white/5` etc.). Pages not yet migrated
rely on them. In migrated markup:

- never use `text-white` (use `text-secondary-foreground` on dark surfaces),
- never use `twilight-*`, `neon`, `bg-card-gradient`, `text-white/NN`, `bg-white/NN` or `border-white/NN`,
- use tokens above instead of raw grays where a token exists.

## Must-keep behaviour when restyling a template

- Every `{% url %}`, form `action`/`method`, input `name`, `{% csrf_token %}`,
  context variable, `{% block %}` name, element `id`, `x-data`/`@click`/`:class`
  (Alpine), `hx-*` (HTMX) and `data-track*` attribute must survive.
- Text asserted by tests must survive (grep `jobs/tests/` for strings from the template).
- Structured data (`application/ld+json`) blocks and meta blocks stay unchanged.

## No AI slop (hard rules)

The product must read as made by a careful human team, not generated. Reviewers
reject any of these:

**Visual**
- No emoji used as icons or decoration anywhere (📝 💰 🌍 🎪 ✨ 🚀 etc.). Use either no icon
  or a single-colour inline SVG (stroke 1.5, `h-4 w-4`, `text-muted-foreground`), and only
  when it carries meaning (location pin, calendar, external-link).
- No "three feature cards with an icon in a tinted square" rows. No "How it works 1 · 2 · 3"
  numbered-circle sections unless the page is genuinely a multi-step form.
- No gradient text, no two-tone headlines (half the sentence in brand green), no glows,
  no decorative blobs, no sparkle icons, no glassmorphism.
- No vanity stat tiles ("111 joined this month", "32 impact verticals"). Numbers only when
  they help a decision (open roles in this filter, salary range of these jobs) and are correct.
- No centred-everything marketing layouts for tools: the tool itself is the hero, usable
  above the fold.
- Kickers (small uppercase labels) at most once per page section group, never stacked
  buzzwords ("REMOTE · PURPOSE-LED · SOURCE-TRACKED").
- Pills/chips only for real filters or tags, not for decoration rows.
- Job titles from sources may be ALL CAPS: display with the `display_title` filter.

**Copy**
- Plain, specific, short. Say what the thing does and for whom. Sentence case headings.
- Banned words/phrases: unlock, unleash, elevate, empower, supercharge, seamless,
  effortless, journey, dream job/role, level up, land your…, stand out from the crowd,
  in seconds, AI-powered/AI-crafted (say what it does instead), cutting-edge,
  game-changer, perfect position, align(s) with your craft/passion, purpose-driven
  professionals (prefer "people"), "Want to …?" headline questions, exclamation marks.
- No invented claims (priority support, guarantees, counts we don't measure).
- Prices and limits must match the product: Free = 1 Scout + 3 tool generations;
  Pro = $10/month with 7-day free trial or $24 for 3 months; employers $99 posting /
  $49 feature.

**Modern quality bar**
- Typographic hierarchy does the work: one H1, generous whitespace, 60–75ch measure for
  reading text, `text-balance` on headings, `tabular-nums` for numbers.
- Real content first: real jobs, real organizations, real counts near the top.
- Interactions: visible focus, hover states, loading states for async actions,
  empty states that tell people what to do next.
- Dense-but-calm lists (Linear / Read.cv / Welcome to the Jungle feel), not card soup.

## v3: calm, neutral, app-like (supersedes earlier visual guidance)

Reference: the ChatGPT/Codex apps and the S-Process sandbox. Reference pages in this
repo: `templates/jobs/home.html` and the nav in `templates/base.html`.

- **Colour**: neutral warm-white background, white cards, ink text and ink primary
  buttons. No lime/brand colours except the logo mark. Colour only for meaning:
  amber = featured/warning, emerald = success, red = error, blue = focus ring.
  Never use `brand-*`, `bg-primary` as decoration, `text-white`, gradients, or coloured
  icon tiles.
- **Borders are rare.** Use spacing and surfaces between page sections. Lists use
  hover-fill rows inside `list-surface`, which draws quiet hairlines between rows;
  tables may keep row lines.
- **Surfaces**: `card` (white, soft shadow, rounded-2xl) or `surface` (muted fill,
  rounded-2xl). Never nest a card inside a card. Never put `btn-secondary` on a `surface`
  (it disappears): use `btn-primary` or `btn-outline` there.
- **Headers are small.** Every page header = optional breadcrumb + `page-title` + one
  `page-lede` line, then content. No hero bands, background washes, or bordered header
  sections. Only the homepage H1 may be larger (`text-3xl sm:text-[2.25rem]`). Never
  `text-4xl/5xl` elsewhere. No uppercase letter-spaced kickers; if a label is needed use
  `.kicker` (now small, sentence case, muted).
- **Type**: Geist, 15px base. Headings `font-semibold` (never `font-bold`/`extrabold`),
  `section-title` for sections, `text-sm`/`text-[13px]` for meta.
- **Controls**: buttons are pills (`btn` = h-9, `btn-sm` h-8, `btn-lg` h-11). Inputs are
  filled (`input`/`select`/`textarea`), borderless until focus, rounded-xl. Chips/filters:
  `rounded-full bg-muted px-3 py-1.5 text-[13px]`, selected = `bg-foreground text-background`.
  Custom dropdown triggers use `input` styling plus their own chevron.
- **Radius**: rounded-full for buttons/chips, rounded-xl for inputs and rows,
  rounded-2xl for cards/surfaces. No sharp corners, no `rounded-md` boxes.
- **Density**: comfortable, not cavernous: sections `py-8 sm:py-10` (`.section`), cards
  `p-5 sm:p-6`, gaps of 4-6 between blocks.

## v3.1: colour with meaning

The neutral base stays. Colour is added only where it tells people something:

- **Impact-area tones.** Each area family has one soft tone (`tone-green` climate,
  `tone-rose` health, `tone-indigo` AI safety/tech, `tone-amber` energy, `tone-orange`
  animal welfare, `tone-sky` education, `tone-violet` policy/rights, `tone-red`
  humanitarian, `tone-teal` development/EA, `tone-stone` other). Pick it with the
  `area_tone` filter; never hard-code a colour for an area.
- **Quiet by default.** Area colour lives only in the small `tone-dot`; `tone-chip` labels
  are neutral (Linear-style). Organization avatars (`avatar` + `initials`) are neutral grey.
  Tinted `tone-tile` blocks are for rare, deliberate use, not lists.
- Components: `tone-chip` (area tag), `tone-dot`, `tone-tile` (tinted link tile),
  `tone-text`, `avatar`/`avatar-sm`, `verified` (green check for roles confirmed on the
  employer's own board).
- Still no gradients, glows, emoji icons or decorative icon tiles. Text on tones uses the
  700/800 shade for contrast. Tone classes are safelisted in `tailwind.config.js` because
  they are chosen in Python.

### Homepage pattern

One hero (headline, Scout composer, a single trust pill) beside one product card: a tabbed
preview of real roles (Latest + the three biggest areas, four roles each, one per
organization). Below it: a single line of hiring organizations, featured roles only when
someone paid, two short cards (seekers, employers), one line of popular searches. Don't add
more lists to the homepage; link to them instead.

## Mobile and interaction quality (October 2026)

Keep the existing neutral design rather than introducing another visual theme.
The shared stylesheet provides:

- Balanced heading wrapping, with the same Geist hierarchy on every page.
- At least 44px height for buttons, tabs, filter triggers and job sorting below 768px.
  Text links and informational tags remain compact; the navigation menu is 44px wide.
- 16px editable text on mobile to avoid Safari's automatic input zoom. Body and metadata
  keep their existing sizes; desktop controls retain their compact rhythm.
- Visible keyboard focus and reduced-motion support that removes animated transitions
  without hiding controls or notifications.

Use component classes before adding page-specific styling. Preserve form names, URLs,
accessible labels, tracking attributes, Alpine and HTMX bindings during changes.
Build with `npm ci && npm run build` from `frontend`; generated CSS and local vendor
assets belong in the release.

## Admin workspace (October 2026)

The Django admin is an Operate surface using the existing neutral Remote Impact
theme, Geist and native Django forms. Keep the public semantic colour tokens;
admin spacing, density and sizing tokens live in `static/admin/workspace.css`.
This extends the product theme instead of introducing Alpine branding.

- Daily navigation groups hiring, people, Scouts/subscriptions and publishing.
  Employer funnel events live under Hiring; customer funnel events live under
  People. Both links follow the existing model permissions.
  Reports and tools expand on demand. Advanced records is a searchable, complete
  permission-filtered registry: hide clutter from navigation, never unregister
  models or delete data just to simplify a screen.
- The workspace shows workflow links and the current staff member's recent
  changes. Avoid unmeasured counts or costly dashboard queries on every page.
- Account names link to the account admin and show email only when that record is accessible;
  profile links remain separate. Long headlines truncate with the full value in
  the title and on the profile. Legacy newsletter consent stays in the record,
  not the default people table or filters.
- Desktop navigation is separate from the work area; mobile uses a native
  disclosure. Tables scroll inside their results region, not the whole page.
  Preserve Django actions, confirmation pages, related-object popups, form media,
  search/filter parameters, history and POST/CSRF logout.
- Keep advanced-record search client-side over the existing allowed model
  names. No extra endpoint, permission bypass or external dependency is needed.

### Scout exploration state

A completed Scout screen without verified matches shows an editorial list of
current roles to explore using the existing `list-surface`, `job-title` and semantic
text tokens. Each card shows employer, location, source pay, date and plain-text
differences; it never uses an eligibility/success badge. The primary action refines
the brief. The same component appears in preview and saved results. Incomplete
review status remains visible above the options; historical result state and
verified-match counts are not rewritten. This is an English product surface.
