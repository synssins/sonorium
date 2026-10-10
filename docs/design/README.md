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
