# The Hopsworks look in an app

Part of **hops-app**: read when styling an app made from the skeleton (FastAPI and a static
JavaScript UI). A Streamlit app is themed in `.streamlit/config.toml`: see
[frameworks.md](frameworks.md).

The skeleton's `static/app.css` is [hops-design](../../hops-design/SKILL.md) as CSS: its tokens
first, then the components built from them. A new app starts as a copy of the skeleton, so the
page is on-brand before anything is added. Extend it; never restyle it.

## Tokens

Every colour, radius and spacing in the app is one of these variables. A new rule uses
`var(--accent)`, never `#1eb182` or another literal.

| Variable | hops-design token | Use |
|---|---|---|
| `--bg` | background | the page and every surface |
| `--tint` | muted | a tinted region, a hovered row |
| `--line` | border | hairlines: dividers, table rules, inputs |
| `--text` | text | body and headings |
| `--muted` | muted text | labels, metadata, units |
| `--accent` | accent | the one primary action, the selection, a positive state |
| `--accent-soft` | accent soft | a selected or positive item's background, the focus ring |
| `--danger` | danger | errors and destructive actions only |
| `--warn` | warn | the middle band of a score bar |
| `--radius` | radius | boxes; controls are 8px |
| `--s1` to `--s6` | spacing | 4, 8, 12, 16, 24, 32px |

The font is the body's stack (`Inter, system-ui, ...`); code, ids and SQL use the hops-design
mono stack. Chart colours come from `window.hopsChart` (`static/chart-theme.js`): `accent` for the
primary series, `context` greys for comparison, `danger` for a breach.

## Components

| Class | What |
|---|---|
| `header.band` with `h1` and `.sub` | the title and one muted line of source |
| `.stats` of `.stat` (`.label`, `.value`) | key figures in one divided row; `.value.accent`, `.value.danger` |
| `.charts` of `figure` with `figcaption` and `.chart > canvas` | charts side by side, stacked when narrow, height reserved |
| `.table-wrap > table`, `td.num`, `td.id` | records; numbers right-aligned, tabular |
| `.bar` (`.track`, `.fill`, `.fill.mid`, `.fill.high`, `.num`) | a score in a row |
| `.status`, `.status.high` | a dot and a word |
| `form`, `label`, `input`, `select`, `button` | one primary button per view |
| `.loading`, `.empty`, `.error` | first load, no data, a failure in words |
| `[aria-busy="true"]` | a list being reloaded, kept and dimmed |
| `.card` | the one boxed level, for content that must stand apart |

A part the skeleton lacks is composed from these tokens and the spacing scale, in `app.css`
after the skeleton's rules.

## The look check

In Hopsable, the `look` tool renders the live app in headless Chromium at 600 and 900px wide
and reports, as text: layout problems (text past its box, overlapping boxes, clipped labels,
large gaps, sideways scrolling), script errors and failed requests, and every colour or font
the page uses that is not in hops-design. Call it once the first full page is written and before
each deploy, and fix all it reports; never script a browser to check the layout.

After `deploy_app`, Hopsable loads the deployed page through the Hopsworks proxy and flags any
`api/` call the page makes that is answered with an HTML page or a non-2xx status: the sign of an
absolute `/api/...` fetch, which reaches Hopsworks instead of the app.
