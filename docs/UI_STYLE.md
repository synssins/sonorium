# Sonorium UI style

The design language agreed with the project owner while redesigning the
Theme Editor (October 2026). Use it for every screen we rework: channels,
speakers, settings pages and dialogs. The approved reference is the Theme
Editor design "C — Aligned table".

## Principles
- **UX first.** Simple, compact but not small, not dense. Clear grouping and order.
- **No explanatory text in the UI.** Short labels. A one-line tooltip is fine; paragraphs are not.
- **Keep the look.** Use the existing palette and components (CSS variables in
  `web/static/css/styles.css`, `.btn` variants, `.badge`, `.icon-btn`, `.seg`,
  toggle switches, sliders). No new colours.

## Windows and pages
- A window grows with the screen up to a sensible **maximum width** (about
  960 px for edit dialogs, 1200 px for settings pages), almost full height on
  large monitors, and fills the screen on a phone.
- **Settings pages are left-justified and share one column width:** a card
  is never wider than a phone screen (420 px); two cards side by side with a
  24 px gap make the column (864 px at most). A list row is as wide as those
  two cards. Title, actions and the Save/Cancel row line up with the column;
  no bars stretch across the screen. Logs is the exception and uses the full
  width. A single-card page (Audio) or dialog (Add network speaker) is one
  card wide.
- **Connection-style cards** (Home Assistant, MQTT, Streaming) are the same
  width and only as tall as their content; side by side on desktop, stacked
  on a phone. Short related fields
  share a row (Broker + Port, Username + Password) and fields never shrink
  below their content.
- **Fixed top and bottom.** The title bar, the item's main fields and the
  list toolbar with its column headers stay in place; **only the list
  scrolls**. The footer bar stays at the bottom.
- Rarely used settings and secondary actions (export, thresholds, etc.) go in
  a **⋯ menu in the title bar**, next to the close button. No separate
  "About"/"Details" sections for one or two fields.

## Fields
- **Size fields to their content.** A name is a single short line; a
  description is one line that grows to two at most. Never a big text box for
  a short value.
- Related short fields share a row (e.g. Name and Categories side by side).
- **Tags/categories: one chip field.** Typing suggests existing values; Enter
  or Tab adds the highlighted one or creates the typed text (spaces allowed);
  × removes a chip; Backspace on an empty input removes the last one. One row;
  chips scroll sideways if they overflow.

## Lists and tables
- **Aligned columns** across the full list, with column headers.
- The **name column is narrow**; the next column sits right against it.
  Names that don't fit **scroll slowly to the end, pause, reset and repeat**
  (ellipsis instead when the user prefers reduced motion).
- Sliders are short, with the value as a % next to them.
- Each row ends with its main toggle (e.g. mute) and then a **⋯ menu
  immediately to its right** (no gap) holding the row's advanced options.
  A small dot on ⋯ shows when an advanced option is on.
- Collections inside a list (e.g. groups) are **collapsible sections** whose
  header row carries the collection's controls **in the same columns** as the
  rows below.
- Drag and drop where it helps, always with a non-drag way too (the ⋯ menu),
  for touch screens.

## Actions
- **Footer:** on the left, the selector the actions depend on (e.g. the preset
  field with Load / New and a small ⋯); on the right **Secondary · Primary ·
  Cancel**, so the primary action (the one that saves and closes) sits next to
  Cancel (e.g. Save preset · Save theme · Cancel).
- **Saving a part saves the whole** where losing work is possible (saving a
  preset saves the theme first).
- **Destructive or bulk actions ask first** with a small "Are you sure?"
  dialog (e.g. Reset all, Delete group).
- Creating a named thing: a small name prompt, and OK saves immediately.

## Phone
- A top bar with the hamburger is **always** there. The hamburger opens the
  menu downward from the bar (scrollable); tapping an item goes there and
  closes it; an item with submenus (Settings) expands in place first; the
  hamburger closes an open menu.
- Cards scale to the screen width.
- Same structure and order as desktop. Table rows become cards; toolbars and
  the footer wrap to extra rows; everything stays reachable without drag and
  drop.

### Screen sizes
- **Phone layout** (every `(max-width: 768px)` / `(max-width: 760px)` rule) is
  used by windows up to 768 px wide, by short screens (height ≤ 480, landscape
  phones) and by touch screens narrower than 1000 px (`pointer: coarse`,
  portrait tablets). The JS checks (`PHONE_QUERY` in app.js) use the same
  conditions. A new phone rule uses the same three conditions.
- **Settings tables in a narrow page:** a settings page is a size container
  (`sp-col`). When the page beside the sidebar is narrower than a table needs
  (mouse windows about 769–1120 px), that table uses its phone layout through an
  `@container sp-col` copy of its phone rule (without the touch sizes): Advanced,
  Plugins and Status below 880 px, the other tables below 740 px. A new table
  with a phone layout gets a copy too.
- **Width ≤ 400:** on a channel card the Theme and Preset labels go above their
  fields, so preset names fit. **Width ≤ 400 and height ≤ 600:** the card is
  445 px tall so Play stays on screen; Recent changes shows one line.
- **Height ≤ 480:** one 48 px header row: the menu button, logo, page title and
  the page's actions.
- **Phone layout at width ≥ 600:** the channel card splits in two halves (3 : 2):
  left Theme, Preset and the sequence / playlist line; right both volume
  sliders, Recent changes and Play. One card per row, never wider than 864 px;
  251 px tall on short screens, 287 px otherwise. The halves are `.ch-a` /
  `.ch-b`, which have no box of their own (`display: contents`) in every other
  layout.
- **Touch screen, width ≥ 1000 (tablets):** the desktop layout, everything
  12.5 % larger (`zoom: 1.125`, `--zoom`). Every `vh` / `dvh` height is divided
  by `var(--zoom, 1)` so it still fits the screen; a new one must be too.
- The channel card grid is the same everywhere: auto-fill, cards at least
  340 px wide.

## Extensible screens
Every screen takes what plugins add the same way (docs/PLUGIN_DEVELOPMENT.md, section 24;
`static/js/ext/contrib.js`). A new screen, list, menu or editor stays extensible when it is
built with the shared helpers:
- **A page** gets its header and top spots from `showView()`, and its title-bar ⋯ the menu
  spot, without doing anything.
- **Rows and cards**: `uiZone(target, 'badges', ref, 'span')` after the row's badges,
  `uiZone(target, 'columns', ref, 'span')` at the end of the name cell, with
  `<span class="ui-zone ui-columns" data-ui-target="…" data-ui-spot="columns" data-ui-head>` in
  the name heading; `uiZone(target, 'info', ref)` after a card's lines.
- **Header buttons**: on a phone the plugins' buttons go in the screen's title-bar ⋯, or in one
  ⋯ for them when it has none: the screen's buttons and help keep their row.
- **⋯ buttons**: `uiAnchor(target, 'menu', ref)` (`'menu settings'` for a thing without an
  editor dialog). Open menus with `spMenu` / `xMenu` / `teMenu` / `tpMenu`; destructive items
  carry `danger` so additions go above them. A dot on ⋯ (`adv-dot`, `sp-adv-dot`) shows any
  change from default; the helpers add plugin values to it.
- **Editors**: `<div class="ui-zone ui-settings" data-ui-target="…" data-ui-spot="settings"
  data-ui-commit>` after the editor's settings; `Sonorium.ui.bind(el, ref)` when it opens and
  `await Sonorium.ui.commit(dialog, ref)` in its Save.
- `ref` is `uiRef(kind, id, facts, theme)`: the object shown, and the facts conditions test.
- Add the target to `TARGETS` in `plugins/ui_contract.py` (its fixed parts and open spots);
  tests/test_ui_contributions.py fails while the web UI and that list disagree.
- Plugins only add, in the open spots. Never draw plugin HTML in a screen, and never give
  additions a look of their own: they use the components above.

## Process
- Structural UI changes are drafted as Claude Design boards first and
  approved by the owner before they're built.
