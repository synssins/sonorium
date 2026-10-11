# Writing style for Sonorium documentation

Rules for the README, the wiki, sonorium.app and the in-app help. They apply to anyone writing for Sonorium, person or AI.

## A. Wording

1. **Use the given wording verbatim.** When wording is supplied for a line, use it exactly: same capitalisation, punctuation and words.
2. **Removed text stays removed everywhere.** When a piece of text is cut, remove the idea from the whole app (templates, scripts, tooltips, messages, help), not only from the one place.
3. **No circular sentences.** Never restate a control's name as its explanation ("Volume: how loud the track plays", "Upload into: uploads into this group"). If a line tells the reader nothing they couldn't guess from the label, cut it.
4. **No ownership words.** "The volume slider", not "the volume's own slider"; "the theme's config", not "the theme's own config". Use "own" only to contrast two owners.
5. **Leave out development topics.** No layout or styling facts ("every channel is a block of the same size"), no colours ("Stop is red"), nothing that is self-explanatory on screen. Document what the user does and needs to understand, not how the interface was designed.
6. **No jokes or asides.**
7. **Terminology:**
   - **track:** one audio file in a theme. Never "take".
   - **sequence:** a theme's presets played in order.
   - **playlist:** themes played in order.
   - **group:** a subfolder of a theme.
   - **floor** and **area:** Home Assistant's terms, not "room".
   - **channel:** never "session" in anything a user reads. Code and API paths keep `session`.
   - **Playback modes:** Unassigned, Background, Intermittent, Ebb & Flow.
   - **Sliders:** Interval (Intermittent), Duration and Rise (Ebb & Flow).

## B. Register

8. **Two registers.**
   - **In-app help** is short and literal ("Reverts unsaved changes." "Saves loaded preset").
   - **The wiki and sonorium.app** follow the cadence and structure of a technical document for an engineer (what it is, what it does, how to use it, an example), written in plain terms, with simple analogies where a concept needs one.
9. **More explanation beats less.** At each step, say what is on screen: what a window contains, what a number means, what changes afterwards.

## C. How-to structure

10. **Every how-to starts at the app's main screen** (the Channels page), never part-way in.
11. **One numbered step per click.** Step text is short and plain: "**1:** Click **Themes** in the menu on the left."
12. **Menus are shown open.** The arrow and number go on the button that opens the menu; the next number goes on the item to click inside it.
13. **Alternatives come after the steps,** spelled out for non-technical readers (for example, how to press a keyboard shortcut).
14. **Sections follow the order the user meets things,** in tiers: section, then subsection, then sub-subsection (for example Background, then Track, then its ⋯ menu).

## D. Screenshots

15. **Callouts are amber arrows and rings with dark outlines,** labelled with the step number, or a letter for a tour of a screen.
    - Arrows take the shortest path to their target.
    - Labels never cover the thing being explained.
    - Arrows don't cross other controls.
16. **Callout positions come from the live page,** not guesses, so they stay accurate after an interface change.
17. **Sensitive data is blurred before anything is drawn.** The blur covers the area and leaves placeholder characters, so the original can't be recovered.
    - Keep the full screenshot when blurring.
    - Cropping for focus is fine elsewhere.
18. **No development or test leftovers** in screenshots.

## E. Accuracy

19. **Check every claim** against the code or a running copy of Sonorium before writing it. If it can't be confirmed, leave it out.
20. **Render and check everything before publishing:** each image after annotating, and each page's images once published.

## F. Versions

21. **Don't name a version** in documentation, pull requests, the wiki or the site until the maintainer has chosen it. The default is a patch release.
