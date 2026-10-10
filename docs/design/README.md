# Approved design boards

Design Component boards (`.dc.html`) approved for the next release. They are
mockups: markup plus a small script for sample data, styled by
`board-styles.css` (a copy of `web/static/css/styles.css`) and a "PROPOSED
(new CSS)" block in each file. They don't run here (they need the canvas
runtime); read them as the specification: markup, class names, wording, the
CSS under "PROPOSED", and the help text (`<p class="hi new">`).

| Board | What it specifies |
|---|---|
| `Sections.dc.html`, `Sections_Phone.dc.html` | The Theme Editor's track list in sections by playback type |
| `Main.dc.html`, `Ebb_Flow_Phone.dc.html` | Ebb & Flow group mode, Pause between plays (group switch and track menu), two-knob Volume |
| `Step_While.dc.html`, `Step_While_Phone.dc.html` | A sequence step's "While playing" view (Rise, Duration, Revert changes) |

## Sections (the Theme Editor's track list)

- Four sections, always shown, even empty, in this order: **Unassigned**,
  **Background**, **Intermittent**, **Ebb & Flow**. Each has a header (name,
  count, one-line description, its own **New group** button; none on
  Unassigned) and its own column headings:
  - Background: Name · Mode · Volume · Mute (no Interval column)
  - Intermittent: Name · Mode · Volume · Interval · Mute
  - Ebb & Flow: Name · Mode · Volume (two knobs: lower = `volume_low`, upper = `volume`) · Duration (no number; "Shorter" and "Longer" under the slider) · Mute
- **Unassigned** (`playback_mode: "unassigned"`): uploads and files added from
  another theme land here. Nothing in it plays. Its rows show the file's
  length and a Mode menu, no sliders.
- A track's **Mode** menu lists Unassigned, Background, Intermittent, Ebb & Flow
  (no Auto). Picking one moves the track to that section at once (display only:
  nothing moves on disk). Dragging a track to another section does the same.
- **Groups** belong to a section and play its way: Background groups are
  Merry-go-round (Crossfade under the name), Intermittent groups take turns
  (Gap), Ebb & Flow groups play every file at once (Pause between plays switch).
  A group's mode is shown as text, never a menu: it can't change section. Make
  one with that section's New group. A track in a group shows its group's mode
  as text; drag it out to change it. Dragging a file into a group sets its
  playback type to the group's.
- **Intrusions** are no longer a group type: a file linked from another theme
  is an ordinary file that can sit in any section or group. The ⚭ preset mark
  (a preset that turns a linked file on) stays.
- The toolbar: Sequences, Preview mix · Upload, Add from another theme, Reset all.
  (New group and Add intrusion are gone from it.)
- Sliders show their section's meaning: Duration is "how long each rise and fall
  takes", higher is longer (the stored `presence` value).

Server-side settings already exist (recording.py, core/theme_metadata.py):
`volume_low`, `rise`, `pause_between`, `pause_min`, `pause_max` per track
(`PUT /api/themes/{id}/tracks/{key}/ebb`), group `mode` (`intermittent`,
`merry_go_round`, `ebb_flow`, `intrusion` until intrusions are retired),
`pause_between`, `rise` (`PUT /api/themes/{id}/groups/{name}`), and
`playback_mode: "unassigned"`.

## Channels page

`Channels_Blocks.dc.html`, `Channels_Blocks_Phone.dc.html` (approved). Each channel is a block, all
exactly the same size (fixed height), three per row on desktop, one on a phone:

- Header: the theme's icon, the channel name, its speakers under it, a Playing / Stopped badge,
  ✎ (opens the channel's Edit dialog) and ⋯ (Delete and the rest; the separate Edit and Delete
  buttons go). The page title shows the channel count, like Themes.
- Two shortened pickers (the same psel list as today, with the preset marks ★ ⚭ and the sequence
  icon), and to their right two mode buttons stacked: a palette (play a theme: the pickers are
  Theme and Preset) and the sidebar's playlist icon (play a playlist: Playlist and Playing). The
  active one is highlighted.
- A "Now" line, always there: "Step 2 of 2: <b>OH S***</b> · next in 3:12" with a Next button
  for a sequence; "Entry 2 of 5 · next in 12:40" with Next for a playlist; otherwise
  "No sequence or playlist" (muted).
- Volume slider and percentage.
- "Recent changes": the channel's last events with their times (started, stopped, theme,
  preset, sequence step, playlist entry; not Ebb & Flow movement), in a fixed-height box.
- A full-width Play button at the bottom, accent coloured; while playing it is Stop, highlighted
  in red (btn-danger), not grey. A playing block has an accent border.

## A sequence step's While playing view

`Step_While.dc.html`, `Step_While_Phone.dc.html` (approved, with these later decisions that
override the board where they differ):

- The sequence editor's Advanced dialog for a step (functions tx* in app.js; opened from a
  preset row's ⋯ › Advanced… in the Sequences editor, sq*) gets a first tab **While playing**
  (with a count of changed values), then the existing **Coming in** and **Leaving <preset>** tabs.
- While playing lists the theme's tracks exactly like the Theme Editor: the same sections
  (Unassigned is left out: nothing in it plays), the same groups, rows and controls (Mode text,
  Volume (two knobs for Ebb & Flow), Interval / Duration (Shorter … Longer, no number), Mute),
  a group header with its Volume, Interval/Duration, Mute, and Gap (Intermittent) or Pause
  between plays (Ebb & Flow). An Ebb & Flow track's ⋯ menu: Rise (Gradual … Sudden), Pause
  between plays, Pause (low–high seconds). An Ebb & Flow group's ⋯ menu: Same Rise for every
  file (+ its Rise slider). No moving tracks, no uploads, no new groups, no fades, no Gapless.
- Each value shows the preset's unless this step changes it; a changed value is shown in the
  accent green (slider value text, select border, mute button ring, gap inputs, switch label).
  A value set back to the preset's counts as unchanged again.
- **Revert changes** (top right of the list, and in each track's and group's ⋯ menu) puts back
  the values as they were when Save was last pressed. Footer: "N changes from the preset" ·
  Save · Cancel.
- Help: an "In a sequence step" part (see the board's <p class="hi new"> lines; "Back to the
  preset" there is now "Revert changes": "puts back the settings as they were when Save was
  last pressed: for one track or group in its ⋯ menu, or for everything at the top of the list.
  A value set back to the preset's follows the preset again.").
- Storage/API (already on the server): GET/PUT /api/v2/themes/{id}/sequences/{sid}/while/{preset}
  with {"tracks": {key: {volume, volume_low, presence, muted, rise, pause_between, pause_min,
  pause_max}}, "groups": {name: {volume, presence, muted, gap_min, gap_max, pause_between, rise}}};
  every field listed, null = the preset's value. Channels on that step follow a save.
