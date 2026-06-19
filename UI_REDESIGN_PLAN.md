# OpenBooks UI Redesign Plan — Dashboard & Budget Flow

*Status: proposal. Author: assistant (Claude). Scope: `index.html` single-file SPA. Goal: raise visual polish and narrative clarity, especially the Dashboard landing and the Budget user flow, without breaking the data contract ("leads, never findings") or the existing API surface.*

---

## 0. Design principles (what "nicer" means here)

1. **One story per screen.** Each tab should answer one question in its first viewport. The Dashboard answers *"where does authorized money diverge from spent and audited money?"* Budget answers *"who was authorized what, and how does it compare?"*
2. **Progressive disclosure.** Hero number → supporting chart → drill-down table → row-level modal. Never show a 20-column table before the headline.
3. **Consistent visual grammar.** Three colors are load-bearing and already defined as CSS vars: `--budget` (blue), `--actual` (green), `--audit` (red). Every chart, pill, and legend must use them the same way everywhere.
4. **Confidence is visible.** Match scores, estimate-vs-actual, and FY-basis caveats get a consistent badge treatment, not buried prose.
5. **Polish details:** smooth number count-ups, skeleton loaders instead of a bare spinner, hover affordances on every clickable element, and a tasteful empty/error state.

---

## 1. Design tokens & global polish (foundation — do first)

These changes are cheap and lift the whole app:

- **Type scale.** Introduce a small modular scale (`--fs-hero: 30px; --fs-h2: 18px; --fs-body: 13px; --fs-meta: 11px`) and apply consistently. Today font sizes are inline and ad hoc.
- **Spacing scale.** `--sp-1: 4px … --sp-6: 24px`. Replace one-off `margin:14px 0 6px` inline styles.
- **Elevation.** Two card shadows (`--shadow-sm`, `--shadow-md`) and a single `--radius: 10px`. Cards currently mix `7px`/`8px`/`9px`/`10px` radii.
- **Number formatting.** `fmt()` already abbreviates ($1.2B). Add `fmtFull()` for tooltips (exact dollars) so hovering any abbreviated figure shows the precise value — important for a fiscal-credibility tool.
- **Count-up animation.** A tiny `animateValue(el, from, to, ms)` helper for hero numbers. ~20 lines, big perceived-quality gain.
- **Skeleton loaders.** Replace the single `spinner` with shimmer placeholders shaped like the content that's coming (hero cards, table rows). Add a `.skeleton` CSS class with a keyframe shimmer.
- **Reusable components** (JS string-builders, since there's no framework): `kpiCard()`, `barRow()`, `confBadge()`, `sectionHeader()`, `emptyState()`. Today these patterns are copy-pasted. Centralizing them is the single biggest maintainability win and guarantees visual consistency.

---

## 2. Dashboard redesign (the landing)

### Current state
Hero = three pillars (authorized / actual / flagged), a variance bar, a top-15 dual-bar chart, and an audit-risk heatmap. Clicking an agency opens the Agency 360° modal. It works but reads as four stacked widgets with no narrative spine.

### Target layout (top to bottom)

**A. Headline band — "The $16.4B Question"**
- One sentence, large: *"Arizona authorized **$47.3B** and spent **$63.7B** across 80 matched agencies — a **+34.6%** year-over-year directional shift."* Numbers count up on load.
- A single inline **variance gauge** (horizontal diverging bar centered at 0%) instead of the current two-bar comparison — easier to read "over vs under" at a glance.
- Persistent **methodology chip** (`ⓘ FY-basis & matched-set explained`) that opens a small popover with the caveats currently living only in the footer.

**B. Three triangulation tiles** (keep the pillars, restyle)
- Budget / Checkbook / Audit, each with: hero value, a sparkline (budget = authorized trend, checkbook = 10-yr spend trend, audit = questioned-cost-by-FY), and a one-line "so what."
- Each tile is a button → routes to the matching tab (Budget / Investigate / Audit) with state preserved.
- **New:** add a 4th tile or a ribbon for **Untraceable Spend** (`$98.6B · 21.4%`) now that the endpoint exists — it's a headline-worthy transparency number and currently only reachable via the Audit menu.

**C. "Where the divergence lives" — the dual-bar chart, upgraded**
- Keep top-15 agencies but add: a toggle for sort metric (variance $ / variance % / questioned cost / untraceable %), and inline mini-legends.
- Bars get hover tooltips with exact figures + a "View 360°" affordance.
- Add a subtle gridline + axis labels; right now the magnitude is hard to read.

**D. Audit-risk heatmap → "risk matrix"**
- Reframe as a 2D matrix: x = variance %, y = questioned cost (or untraceable %), bubble size = total spend, color = audit risk. This turns a flat heatmap into an analyst's quadrant chart ("high spend + high questioned cost + high variance" = top-right = look here first"). Still clickable to the 360° modal.

### Agency 360° modal polish
- Already strong (3 triangulation panels + traffic-light). Improvements:
  - Add the **finding → Trace spend** drill-down (now built) directly in the Audit panel, not just via the findings tab.
  - Add an agency-scoped **untraceable-spend** line to the Checkbook panel (`unattributed_spend(agency=...)`).
  - Animate the modal in (translateY + fade) instead of instant show.

---

## 3. Budget flow redesign (the biggest UX gap)

### Current state
A `Budget` tab with four sub-tabs (overview / variance / funds / search) rendered from `/api/budget`. Sub-tabs are switched by buttons; content is dense tables. It's functional but feels like a data dump and the sub-tab model is easy to miss.

### Target: a guided three-step flow

**Step 1 — Overview ("the shape of the budget")**
- Replace the 4-stat grid with 4 polished KPI cards (count-up, sparkline, consistent color grammar).
- Add a **treemap or horizontal stacked bar** of authorized $ by cabinet/agency — the single most useful "shape" view and currently missing. Budget data already carries agency totals.
- A prominent search/jump box: "Find an agency or fund…" with type-ahead.

**Step 2 — Compare ("authorized vs actual")**
- Promote variance from a sub-tab to the centerpiece. A sortable, filterable table where each row has an **inline diverging variance bar** (reuse the dashboard gauge component).
- Filter chips: *over / under / matched-only / has-audit-findings*.
- Each row expands in place to show the agency's fund sources and a "View 360°" button — no context loss.
- **Persistent caveat banner** that variance is directional YoY, not overage — styled as an info bar, not buried.

**Step 3 — Drill ("where do the dollars sit?")**
- Merge the "funds" sub-tab here. Fund-source bars (the `fundbar` component already exists in the 360° modal — promote it to a shared component).
- Add breadcrumb navigation (Budget › Agency › Fund) so users know where they are.

### Budget flow mechanics
- Replace free-floating sub-tab buttons with a **segmented control** (pill group) that reads as a stepper.
- Preserve sub-tab + scroll state when opening and closing the 360° modal.
- Deep-linkable via URL hash (`#budget/variance?agency=DHS`) so a finding can be shared.

---

## 4. Cross-cutting: confidence & provenance surfacing

(Addresses the credibility gap noted in review.)

- **Match-confidence badge.** Anywhere a budget↔checkbook↔AG match is shown, render a small badge: ● exact / ◐ strong / ○ weak (token-jaccard score on hover). The data already carries `match_score` / `match_method`.
- **Estimate vs actual.** Questioned-cost figures already carry `questioned_cost_confidence`; render `est.`/`proj.` superscript consistently and never show a projection as a confirmed loss in a hero position.
- **"Show the queries" everywhere.** The Ask tab already has a `workTrail` (collapsible tool-call list). Promote that component so any AI-or computed surface can expose "here's exactly what was queried" — a key trust feature for journalists/bondholders.
- **Staleness flags.** AG data for many agencies stops at FY2019–2024 while spend runs to FY2025. Add a subtle "audit data through FYxx" stamp wherever AG figures appear.

---

## 5. Accessibility & responsiveness

- Tab `role`/`aria-selected` already present; extend to the budget segmented control and modal (focus trap, `aria-modal` already set — add focus return on close).
- Color is currently the only signal for over/under and tier; add a glyph (▲/▼, ●) so it survives color-blindness and grayscale printing.
- The two-column transparency layout already collapses at 760px; apply the same breakpoint discipline to the dashboard charts and budget tables (horizontal scroll wrappers for wide tables on mobile).
- Respect `prefers-reduced-motion` for the count-ups and modal transitions.

---

## 6. Suggested sequencing

| Phase | Work | Risk | Payoff |
|------|------|------|--------|
| **1** | Design tokens, shared components (`kpiCard`, `barRow`, `confBadge`), skeleton loaders, count-ups | Low | High — lifts everything |
| **2** | Dashboard headline band + variance gauge + Untraceable tile | Low | High — first impression |
| **3** | Budget guided flow (stepper, inline variance bars, treemap) | Med | High — the weakest flow today |
| **4** | Risk matrix chart + 360° modal enrichments (drill-down, untraceable line) | Med | Med |
| **5** | Confidence badges, staleness flags, "show queries" everywhere | Low | High for credibility |
| **6** | A11y pass, reduced-motion, mobile table wrappers | Low | Med |

Each phase is independently shippable and leaves the app working. Phase 1 is the unlock — once the shared components exist, phases 2–4 are mostly composition rather than net-new CSS.

---

## 7. Explicitly out of scope (for now)
- No framework migration. The single-file vanilla-JS SPA is a feature (zero build, trivial deploy); the redesign stays within it.
- No new color identity — the budget/actual/audit triad is good and already wired to CSS vars.
- No charting library unless a chart genuinely needs it (the treemap and risk matrix may justify a tiny dependency or ~80 lines of hand-rolled SVG; prefer hand-rolled to keep the no-build property).
