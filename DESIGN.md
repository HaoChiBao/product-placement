# Unskippable design system

One system for brand flow (Annie / provider), campaigns, and Watch. Two surfaces share the same tokens: **brand** (light) and **cinema** (dark Watch shell).

## Surfaces

| Surface | Where | Class |
|---------|-------|--------|
| Brand | `/`, `/research`, `/videos`, `/review/…`, `/campaigns` | default (`:root`) |
| Cinema | `/watch`, `/watch/[slug]`, player | `.theme-cinema` |

## Color tokens

CSS variables → Tailwind theme keys.

| Token | Brand | Cinema | Tailwind |
|-------|-------|--------|----------|
| `--background` | `#f5f6f3` | `#070809` | `bg-background` |
| `--foreground` | `#141815` | `#f3f4f2` | `text-foreground` |
| `--card` | `#ffffff` | `#121416` | `bg-card` |
| `--muted` | `#eceeea` | `#1a1d21` | `bg-muted` |
| `--muted-foreground` | `#5d655e` | `#9aa3a0` | `text-muted-foreground` |
| `--faint` | `#8a928b` | `#6b7370` | `text-faint` |
| `--border` | `#e0e4df` | `rgba(255,255,255,0.08)` | `border-border` |
| `--primary` | `#141815` | `#f3f4f2` | `bg-primary` |
| `--primary-foreground` | `#f5f6f3` | `#070809` | `text-primary-foreground` |
| `--accent` | `#13a452` | `#3dd67f` | `text-accent` / `bg-accent` |
| `--accent-foreground` | `#0a6b33` | `#05210e` | — |
| `--accent-soft` | `#e2f3e8` | `#15301f` | `bg-accent-soft` |
| `--warning` / `--warning-soft` | `#b86e12` / `#fbefd9` | `#e6a24a` / `#33260f` | — |
| `--destructive` / `--destructive-soft` | `#d93a40` / `#fbe6e7` | `#f2555a` / `#3a1618` | — |
| `--stage` / `--stage-2` / `--stage-line` / `--stage-ink` / `--stage-muted` | review player chrome (always dark) | same | used on review |

Account accent (avatar): `#c8641c`.

## Type

Fonts (Next/Google): **Bricolage Grotesque** display, **Geist** body, **Geist Mono** mono.

| Role | Size | Weight | Family |
|------|------|--------|--------|
| Display h1 | `clamp(26px, 3.2vw, 36px)` / lh 1.1 | 700 | display |
| h2 | 20px / 1.2 | 700 | display |
| h3 | 15px | 600 | body |
| Body | 14px / 1.5 | 400–500 | body |
| Eyebrow | 11px, uppercase, tracking 0.08em | 500 | body |
| Mono / ticks | 10.5–12px | 400–500 | mono |

## Spacing & layout

- Page shell max width: `1440px`; horizontal pad `24px` (`16px` <720px).
- Section gaps: 22–28px; form fields: 14px; dense lists: 8–10px.
- Brand onboard / research: 2-col → 1-col at 1080px.
- Review: stage + 380px side → stacked at 1080px.

## Radii & elevation

| Token | Value | Use |
|-------|-------|-----|
| `--radius` | `14px` | panels / cards |
| `--radius-md` | `10px` | inputs, thumbs, search |
| `--radius-sm` | `8px` | buttons |
| `--radius-xs` | `5–6px` | chips, marks |
| `--shadow` | soft 1px + 24px blur | panels, toast |

## Motion

| Kind | Duration | Easing |
|------|----------|--------|
| Micro (hover, focus ring) | 120–160ms | `ease-out` |
| Enter / list stagger | 200–320ms | `ease-out` |
| Page / panel | 280–400ms | `[0.22, 1, 0.36, 1]` |
| Stagger children | 40–60ms | — |

`prefers-reduced-motion: reduce` → no animation.

### Motion Primitives (in `web/src/components/motion-primitives/`)

| Component | Use |
|-----------|-----|
| `TextEffect` | Hero / page titles (preset `fade-in-blur`, per word) |
| `TextShimmer` | Loading / “researching” labels |
| `AnimatedGroup` | Video grids, campaign tiles, episode rails (preset `blur-slide` or `fade`) |
| `InView` | Section reveal on Watch rails / long pages |
| `TransitionPanel` | Campaigns tabs content swap |
| `MorphingDialog` | Videos detail drawer (card → focused panel) |

Keep motion restrained: one title effect + staggered lists + hover lift. No continuous gimmicks.

## Shared components (`web/src/components/ui/`)

| Component | Role |
|-----------|------|
| `Button` | primary / secondary / ghost / stage; sm / md / lg |
| `Input` / `Textarea` / `Label` / `Field` | form controls |
| `Card` / `Panel` | bordered surface containers |
| `PageShell` | max-width wrap + optional sticky top |
| `EmptyState` / `Skeleton` | empty + loading |
| `Badge` / `Tag` | status chips |
| `TopBar` | brand nav + step rail |
| `WatchChrome` | cinema nav (same logo mark) |

## Layout patterns

1. **Brand funnel** — TopBar (+ steps) → `PageShell` → 1–2 column content.
2. **Marketplace** — head + search → animated grid → morphing detail.
3. **Review** — full-bleed stage (cinema tokens) + decision side (brand surface).
4. **Watch** — `.theme-cinema` shell → full-bleed hero → horizontal rails → player.

## Do / don’t

- Do use tokens and shared UI; one accent green across surfaces.
- Don’t reintroduce per-route CSS files or a second palette.
- Don’t invent speculative layout abstractions beyond the list above.
