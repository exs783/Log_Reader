---
name: Pixhawk Log Analyzer
description: Light, restrained operate-mode dashboard for ArduPilot flight-log diagnostics
colors:
  bg: "#f6f7f9"
  surface: "#ffffff"
  surface-2: "#f0f2f5"
  border: "#e3e6eb"
  border-strong: "#d3d8e0"
  text: "#1a2130"
  text-dim: "#667085"
  text-faint: "#98a2b3"
  accent: "#2f6fed"
  accent-tint: "#eaf0fe"
  green: "#16a34a"
  green-tint: "#eaf7ee"
  green-text: "#0f6b32"
  amber: "#d97706"
  amber-tint: "#fdf3e3"
  amber-text: "#92450a"
  red: "#dc2626"
  red-tint: "#fdecec"
  red-text: "#9c1c1c"
  accent-hover: "#2258c9"
  accent-text: "#1d4ba8"
typography:
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  title:
    fontFamily: "Inter, sans-serif"
    fontSize: "18px"
    fontWeight: 700
    letterSpacing: "-0.01em"
  stat-value:
    fontFamily: "Inter, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    fontFeature: "tabular-nums"
  label:
    fontFamily: "Inter, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    letterSpacing: "0.03em"
  display:
    fontFamily: "Inter, sans-serif"
    fontSize: "56px"
    fontWeight: 800
    fontFeature: "tabular-nums"
rounded:
  xs: "6px"
  sm: "8px"
  md: "10px"
  lg: "12px"
  pill: "999px"
spacing:
  sm: "8px"
  md: "16px"
  lg: "28px"
components:
  nav-tab-active:
    textColor: "{colors.accent}"
    typography: "{typography.body}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "20px"
  stat-box:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "16px"
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: "9px 18px"
---

# Design System: Pixhawk Log Analyzer

## Overview

**Creative North Star: "The Clean Instrument Panel"**

Replaces the previous sci-fi HUD treatment (near-black scanline background, Orbitron/Share Tech Mono type, cyan/amber glow, bracket-corner ornaments, angled clip-path buttons) with a plain, light engineering dashboard: the kind a small team reads quickly between flights, not a cockpit prop. Confirmed rejection: no dark theme, no glow/scanline decoration, no monospace-as-costume, no bracket ornaments.

Density stays high (11 analysis tabs, dense stat grids, full-width Plotly charts) but every surface is flat white/near-white with a single restrained accent (blue) reserved for the primary action, active nav state, and links between numbers and meaning (severity colors on top of that). This is an Operate surface — clarity and scan speed outrank expression.

**Key Characteristics:**
- Light neutral ground (`#f6f7f9`) with white card surfaces — never near-black.
- One accent (`#2f6fed`) used sparingly: primary button, active tab, info-panel tint.
- Semantic severity colors (green/amber/red) carry meaning, not decoration.
- One typeface (Inter) for everything, including numeric stat values (tabular figures).
- No glow, no scanline texture, no bracket corners, no clip-path buttons.

## Colors

Restrained strategy: neutrals carry the surface, one accent carries interaction, three semantic colors carry flight-quality meaning.

### Primary
- **Signal Blue** (`#2f6fed`): primary upload button, active nav-tab underline/text, info-panel background tint (`#eaf0fe`), neutral chart lines.

### Neutral
- **Panel Gray** (`#f6f7f9`): page background.
- **Card White** (`#ffffff`): every card, stat box, summary item, nav bar, header.
- **Fog Gray** (`#f0f2f5`): the rare secondary surface.
- **Hairline** (`#e3e6eb`): all 1px borders.
- **Hairline Strong** (`#d3d8e0`): dashed empty-state border, scrollbar thumb.
- **Ink** (`#1a2130`): primary text and stat values.
- **Slate** (`#667085`): secondary text, axis labels, upload status.
- **Mist** (`#98a2b3`): stat labels, placeholder/faint text.

### Named Rules
**The One Accent Rule.** Signal Blue appears on the primary action, the active tab, and info-panel tints only — never as a page-wide theme color. Everywhere else, meaning comes from the semantic (green/amber/red) trio, not from the brand accent.

### Chart Palette (Plotly data series only)
Line/series colors are a separate category from UI chrome — they exist to keep 2-6 simultaneous traces distinguishable on a white plot background, not to carry brand meaning. In use: `#dc2626` `#0f9488` `#2f6fed` `#16a34a` `#d97706` `#7c6ff0` `#db2777` `#0891b2`. Each accessible-contrast trace color is intentional and may repeat across tabs (e.g. red always maps to the "primary" series like AccX/Voltage/Altitude); do not fold these into the UI accent token.

### Text-on-tint colors
Alert and info-panel text uses a darker in-hue shade over its own tint background rather than the flat brand color, so body copy clears 4.5:1 contrast: `{colors.green-text}` `#0f6b32`, `{colors.amber-text}` `#92450a`, `{colors.red-text}` `#9c1c1c`, `{colors.accent-text}` `#1d4ba8`. `{colors.accent-hover}` `#2258c9` is the primary button's hover state (darkened Signal Blue).

## Typography

**Body Font:** Inter (with `-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`)

**Character:** One workhorse UI sans across the whole surface, including chart axis labels and stat values — no display face, no mono affectation. Numeric stat values use `font-variant-numeric: tabular-nums` so digits align across the stat grid.

### Hierarchy
- **Display** (800, 56px, tabular-nums): the flight-quality score number only.
- **Title** (700, 18–20px, −0.01em): section titles and the score's verdict word.
- **Card heading** (600, 13–15px): card `<h3>`, header wordmark, info-panel `<h2>`.
- **Stat value** (700, 22px, tabular-nums): the number in every stat box.
- **Body** (400, 13–14px): info-panel copy, summary items, event items, nav tabs.
- **Label** (600, 11–12px, 0.03em tracking, uppercase where noted): stat labels, header sub/status text, footer, chart axis labels.

Type scale in use: 11 / 12 / 13 / 14 / 15 / 18 / 20 / 22 / 56px — a single small-step ramp, no size chosen outside it.

## Layout

Same structural grammar as before: sticky header (64px) → sticky upload bar → sticky horizontal nav (11 tabs, scrolls on overflow) → centered container (max-width 1440px, 28px padding) → per-tab page with an info-panel, a stat grid (`auto-fit, minmax(150px,1fr)`), and a full-width card holding one Plotly chart. Nav and header both stay `position: sticky` so the tab bar is always reachable on a tall page. No responsive breakpoints were added beyond the existing `auto-fit` grids, which already reflow on narrow viewports.

## Elevation & Depth

Flat by default; the only depth cue is a soft ambient shadow on cards, which deepens slightly on hover to signal interactivity. No colored glow, no zero-offset halo.

### Shadow Vocabulary
- **Resting** (`0 1px 2px rgba(16,24,40,0.05)`): default state for `.card` and `.stat-box`.
- **Hover** (`0 4px 16px rgba(16,24,40,0.08)`): `.card:hover` only.

### Named Rules
**The Flat-By-Default Rule.** Every surface sits flush at rest (`--shadow-sm`); shadow only deepens in direct response to hover.

## Shapes

Rounded-rectangle language throughout: 12px radius on cards/stat-boxes/summary-items, 10px on info-panels/alerts/events, 8px on the primary button, pill (999px) on the status badge. No clip-path, no angled cut corners, no bracket ornaments — those were the previous HUD's signature and are explicitly retired.

## Components

### Buttons
- **Shape:** 8px radius.
- **Primary (`.file-input-label`):** solid Signal Blue background, white text, 600 weight, 9px/18px padding.
- **Hover:** background darkens to `#2258c9`; no glow, no shadow pop.

### Cards / Containers
- **Corner Style:** 12px radius.
- **Background:** white on the `#f6f7f9` page ground.
- **Shadow Strategy:** see Elevation & Depth — resting → hover only.
- **Border:** 1px `#e3e6eb` hairline.
- **Internal Padding:** 20px.

### Stat Boxes
- **Style:** white card, 12px radius, 1px hairline border, no accent top-border (previous version's colored `border-top` accent was removed as a banned surface habit).
- **Content:** uppercase 11px label (Mist) → 22px bold tabular-nums value (Ink) → optional unit suffix (Slate, 12px).

### Navigation
- **Style:** horizontal tab row, 13px Inter, Slate at rest, Ink on hover, Signal Blue + 2px bottom border + 600 weight when active. Disabled tabs (`no log loaded`) drop to 40% opacity.

### Alerts / Info Panels
- **Style:** solid tint background (no border-left accent bar), 10px radius, semantic text color per severity (`success`/`warning`/`danger`/`info` map to green/amber/red/blue tints).

## Do's and Don'ts

### Do:
- **Do** keep every DOM id and class the inline JS depends on (`#log-file`, `#upload-status`, `.nav-btn`, `.page`, `#<tab>-plot`, `#<tab>-stats`, `.stat-box`, `.alert-*`) — the backend contract and chart-generation functions read/write these directly.
- **Do** keep Plotly chart backgrounds white (`plot_bgcolor`/`paper_bgcolor: #ffffff`) and axis/grid colors from the Neutral group so charts match the surrounding light UI.
- **Do** reserve Signal Blue for the one primary action and active states; use the green/amber/red trio for all severity/quality signaling.

### Don't:
- **Don't** reintroduce the dark HUD system (near-black background, cyan/amber glow, Orbitron/Share Tech Mono, scanline texture, bracket corners, clip-path buttons) — it was deliberately retired for this light rebuild.
- **Don't** add a colored `border-left`/`border-top` accent bar to cards or stat boxes; differentiate through typography and the tint colors instead.
- **Don't** add a build step or split this into multiple files — Flask serves `log_dashboard.html` as a single static file via `send_from_directory`.
