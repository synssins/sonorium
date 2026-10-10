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
  field with Load / New and a small ⋯); on the right **Primary · Secondary ·
  Cancel** (e.g. Save theme · Save preset · Cancel).
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

## Process
- Structural UI changes are drafted as Claude Design boards first and
  approved by the owner before they're built.
