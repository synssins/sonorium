# CHANGELOG

<!-- version list -->

## v1.4.0-dev.7 (2026-10-09)

### Bug Fixes

- Wider New/Edit Channel dialog without a scroll box inside it
  ([`402a4f9`](https://github.com/synssins/sonorium/commit/402a4f9a961209ec398d523233fe42a4c0065a1b))


## v1.4.0-dev.6 (2026-10-09)

### Bug Fixes

- Network discovery works when another mDNS service holds port 5353
  ([`3fbd6f1`](https://github.com/synssins/sonorium/commit/3fbd6f1b84c239be329056da77936ca0fbb73003))

### Continuous Integration

- Queue image builds for the same branch instead of cancelling.
  ([`3fbd6f1`](https://github.com/synssins/sonorium/commit/3fbd6f1b84c239be329056da77936ca0fbb73003))


## v1.4.0-dev.5 (2026-10-09)

### Build System

- **docker**: Add network speaker dependencies to the standalone image
  ([`ce0cc00`](https://github.com/synssins/sonorium/commit/ce0cc008e16447f4345547008320e2c3b5882f99))

### Features

- **standalone**: Discover and stream to network speakers
  ([`d03a487`](https://github.com/synssins/sonorium/commit/d03a4872ee0c30207bb3984ae4963eb136e01cac))

### Testing

- Network speaker IDs, routing, hierarchy and add-on isolation
  ([`ffb7768`](https://github.com/synssins/sonorium/commit/ffb77686c0583c66f89eeb2bbdfa725c1571203b))


## v1.4.0-dev.4 (2026-10-09)

### Features

- Connection settings page for standalone (Docker)
  ([`1a6b6b6`](https://github.com/synssins/sonorium/commit/1a6b6b61df68133ef4b36004f9e7b7643d3956c9))


## v1.4.0-dev.3 (2026-10-09)

### Bug Fixes

- Standalone status and log tidy-ups
  ([`e6ee14c`](https://github.com/synssins/sonorium/commit/e6ee14c83db5c17ff4c9f2096571aa21014fe55b))


## v1.4.0-dev.2 (2026-10-09)

### Bug Fixes

- Standalone runs without Home Assistant
  ([`76b4cd6`](https://github.com/synssins/sonorium/commit/76b4cd61340b9bffa40999e212df53ed7d6e48ba))


## v1.4.0-dev.1 (2026-10-09)

### Build System

- Standalone Docker image published to ghcr.io/synssins/sonorium
  ([`ff2a86c`](https://github.com/synssins/sonorium/commit/ff2a86cfe436c5b61dc31740746532f9d6305cd2))

### Features

- Standalone mode for running Sonorium in Docker without HA Supervisor
  ([`12e3528`](https://github.com/synssins/sonorium/commit/12e3528d955d2ea5560a5b29caabafe8c4ec40fb))


## v1.3.0 (2026-10-09)

### Continuous Integration

- Bump -dev.N instead of the patch version for non-releasing commits on branches
  ([`7559fd4`](https://github.com/synssins/sonorium/commit/7559fd4712b05f953f8bdc851d3506f98bebddff))


## v1.3.1-dev.1 (2026-10-09)

### Chores

- Make the Windows app's Tavern theme match the add-on's
  ([#41](https://github.com/synssins/sonorium/pull/41),
  [`bcf6d0b`](https://github.com/synssins/sonorium/commit/bcf6d0b6a21729fe3d5e1c37dfd3015144fd1c46))

### Documentation

- README updates for 1.3.0; trim old release notes
  ([`2f2382e`](https://github.com/synssins/sonorium/commit/2f2382e72afd8ddba64253b3c3187e2ab6265666))


## v1.3.0-dev.10 (2026-10-09)

### Build System

- Pin PyAV to 19.x
  ([`1df0ca7`](https://github.com/synssins/sonorium/commit/1df0ca79663d0b078e3a64c2f0e8654df49da3fe))


## v1.3.0-dev.9 (2026-10-09)

### Bug Fixes

- Clear the old Sonorium media player's retained MQTT topics
  ([`01930c8`](https://github.com/synssins/sonorium/commit/01930c80751864f0700b8824eb7cdc4e59dcc3f4))


## v1.3.0-dev.8 (2026-10-09)

### Bug Fixes

- Re-subscribe MQTT command topics after the broker reconnects
  ([`745e17d`](https://github.com/synssins/sonorium/commit/745e17dc6ee43400233d4198745fe9e3a8a17fdc))


## v1.3.0-dev.7 (2026-10-09)

### Bug Fixes

- Don't leak the MQTT broker password into debug logs
  ([`dc2ae8b`](https://github.com/synssins/sonorium/commit/dc2ae8b5fc6dfef0ecdf6610c28fc28d09627244))


## v1.3.0-dev.6 (2026-10-09)

### Bug Fixes

- Per-play log at normal level shows only user-relevant events
  ([`c77efd7`](https://github.com/synssins/sonorium/commit/c77efd745d26227ed869070aab17d82a3349a642))


## v1.3.0-dev.5 (2026-10-09)

### Bug Fixes

- Start Cast playback ~5s faster and drop false Cast warnings
  ([`c5abec0`](https://github.com/synssins/sonorium/commit/c5abec0851be0067cc1a9196a9600a10fc8dd5f0))


## v1.3.0-dev.4 (2026-10-09)

### Bug Fixes

- Instrumented-call traces at debug; explicit play lines at info
  ([`b8127df`](https://github.com/synssins/sonorium/commit/b8127df425275ea923ce156c7cda9282c2fdf7ec))


## v1.3.0-dev.3 (2026-10-09)

### Bug Fixes

- Apply the log level to Logfire's console output too
  ([`5dd8510`](https://github.com/synssins/sonorium/commit/5dd851093b27b8b3636911c4114659764d86b1f0))


## v1.3.0-dev.2 (2026-10-09)

### Bug Fixes

- Trim the normal-level log to a summary; move startup detail to debug
  ([`5b90fff`](https://github.com/synssins/sonorium/commit/5b90fffb40f94dfa4e2642c32f16681524446b24))


## v1.3.0-dev.1 (2026-10-09)

### Features

- Log level option; normal log is a summary, details at debug
  ([`dd30f70`](https://github.com/synssins/sonorium/commit/dd30f70af290acf55a06e253c2832d44a4bc882a))


## v1.2.89-dev.7 (2026-10-09)

### Bug Fixes

- Add-on failed to start: bashio::addon.slug doesn't exist
  ([`dd5965d`](https://github.com/synssins/sonorium/commit/dd5965d9d462988485f1b202eec3cfaf8aabc3eb))


## v1.2.89-dev.6 (2026-10-09)

### Bug Fixes

- Copy legacy settings once per install instead of renaming the old folder
  ([#30](https://github.com/synssins/sonorium/pull/30),
  [`eaa314f`](https://github.com/synssins/sonorium/commit/eaa314f3a50b850c64f77907a6a7aa86b4622e8f))

- Drop "(Dev)" from the add-on name and point URLs at this repo
  ([#40](https://github.com/synssins/sonorium/pull/40),
  [`04d967e`](https://github.com/synssins/sonorium/commit/04d967e6155f9688997090289849fd2494bdc75d))

- Keep settings in the add-on's own config folder so uninstall can remove them
  ([#30](https://github.com/synssins/sonorium/pull/30),
  [`cddf0ba`](https://github.com/synssins/sonorium/commit/cddf0bacdfd89b9ffe15ee8bdb474f46a766a1e4))

- Make browsers revalidate the web UI files after updates
  ([#28](https://github.com/synssins/sonorium/pull/28),
  [`cd70f62`](https://github.com/synssins/sonorium/commit/cd70f62912ca9f2de4b97c4f59cf9711642a11f0))

### Documentation

- Clarify why homeassistant_config is mapped
  ([`5eb86c3`](https://github.com/synssins/sonorium/commit/5eb86c342acc5bc88d0ea5604112f7c846334e2a))


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
