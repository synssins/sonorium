# CHANGELOG

<!-- version list -->

## v1.2.89-dev.5 (2026-10-09)

### Bug Fixes

- Detect idle channels by audio pulled, not open connections
  ([#29](https://github.com/synssins/sonorium/pull/29),
  [`05a9fe0`](https://github.com/synssins/sonorium/commit/05a9fe0f5fe02266c5710d5811bbd06a70431da6))

- Refresh themes (and MQTT theme lists) after theme upload/delete
  ([#33](https://github.com/synssins/sonorium/pull/33),
  [`fea1a49`](https://github.com/synssins/sonorium/commit/fea1a492235b25240812d65cce8a6575085b9c20))

- Update the MQTT session selector's state after a rename
  ([#16](https://github.com/synssins/sonorium/pull/16),
  [`27572b8`](https://github.com/synssins/sonorium/commit/27572b8c148ea03d825c5d07160fe46b2d58e12c))


## v1.2.89-dev.4 (2026-10-09)

### Bug Fixes

- Stop channels nobody is listening to, and make Stop All stop them
  ([#29](https://github.com/synssins/sonorium/pull/29),
  [`c629dd1`](https://github.com/synssins/sonorium/commit/c629dd145dcf0fb66b9551c25e1c4089b22f016a))

- Stop crossfade loops from cutting out at the loop point
  ([#38](https://github.com/synssins/sonorium/pull/38),
  [`0e33644`](https://github.com/synssins/sonorium/commit/0e33644ca5fe9c2c384b49b1cb321cb58907173d))


## v1.2.89-dev.3 (2026-10-09)

### Bug Fixes

- Keep numpy below 2.4 so Sonorium runs on basic virtual CPUs
  ([`c493b4a`](https://github.com/synssins/sonorium/commit/c493b4a1015a0e9ba524c067cb0aaa143f8e7433))


## v1.2.89-dev.2 (2026-10-09)

### Bug Fixes

- Wait for MQTT broker at startup and explain VM CPU crashes
  ([`1370fdc`](https://github.com/synssins/sonorium/commit/1370fdc405a9faff7f26dbeee2a82375cad6a839))


## v1.2.89-dev.1 (2026-10-09)

### Bug Fixes

- Don't fail session create/delete when MQTT publish fails
  ([`280c464`](https://github.com/synssins/sonorium/commit/280c464c2b459b14f391467b8661d222f615c216))

### Build System

- Automate add-on versioning with python-semantic-release
  ([`6be2462`](https://github.com/synssins/sonorium/commit/6be2462e4713b382dd739e21063abfafcd1e1aa5))


## v1.2.88 (2026-10-08)

- Initial Release
