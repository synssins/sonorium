# CHANGELOG

<!-- version list -->

## v1.7.3-dev.52 (2026-10-13)

### Features

- **themes**: Bulk theme import, Duplicate theme, unique theme names
  ([`3319a01`](https://github.com/synssins/sonorium/commit/3319a014edbafdb6ae891b902542fad485888f31))

### Testing

- **taps**: Slow-tap and track-tap tests no longer race the tap threads
  ([`581ac91`](https://github.com/synssins/sonorium/commit/581ac9155991e49591c4a323cda1f796d1d7b44c))


## v1.7.3-dev.50 (2026-10-13)

### Bug Fixes

- **ui**: Channel cards drop Recent changes; cards as tall as their content
  ([`9d29a35`](https://github.com/synssins/sonorium/commit/9d29a35a80e08102c55c7a5a2d3fd75c07c72288))


## v1.7.3-dev.48 (2026-10-13)

### Features

- **settings**: Server name (Settings > Advanced > General)
  ([`7898355`](https://github.com/synssins/sonorium/commit/7898355afa451cee19e3670769e0a5f8e1b9f3a6))


## v1.7.3-dev.46 (2026-10-13)

### Features

- **mdns**: TXT name carries the server's name and follows a rename
  ([`15cc848`](https://github.com/synssins/sonorium/commit/15cc84848a3106e64646f1312eb375e417f11237))


## v1.7.3-dev.44 (2026-10-13)

### Features

- **themes**: Theme audio standardized to MP3 320 kb/s, 44.1 kHz, stereo
  ([`6c1399d`](https://github.com/synssins/sonorium/commit/6c1399d68b501e32ef6c8c2b701c5d2eef9c6e27))


## v1.7.3-dev.42 (2026-10-13)


## v1.7.3-dev.40 (2026-10-13)
## v1.7.3-dev.41 (2026-10-13)

### Bug Fixes

- **ui**: Channel card header buttons line up with the buttons below
  ([`515c7cf`](https://github.com/synssins/sonorium/commit/515c7cfcee09da7b7674b153249f6fc293721bed))

- **windows**: Status "Check now" opens Updates and checks there; GitHub rate limit is "busy",
  retried later
  ([`3d167f8`](https://github.com/synssins/sonorium/commit/3d167f84e551f7a19b0f938bc4f9e565ec403cbb))


## v1.7.3-dev.38 (2026-10-13)

### Continuous Integration

- **windows**: A GitHub rate limit in the clean-runner update check warns instead of failing the
  release
  ([`a671baa`](https://github.com/synssins/sonorium/commit/a671baab4cdd1379137fee91826c283493b0ded7))

### Features

- **backups**: Theme backups from the app, per account and phone
  ([`d901989`](https://github.com/synssins/sonorium/commit/d901989d2710aa4fae4a0989cb303dc68360de11))

- **engine**: Stereo in the shared mixer, a server Stereo switch, and a FLAC audio cache
  ([`2342c93`](https://github.com/synssins/sonorium/commit/2342c935e54667937f17259042778284f090e06e))

- **web**: Copy a linked file into the theme; sleep timer on channels
  ([`395e01d`](https://github.com/synssins/sonorium/commit/395e01de75bd04599e34a37a4adbad166670eaee))


## v1.7.3-dev.36 (2026-10-13)

### Bug Fixes

- **core**: Theme packages and Export serve the metadata the server uses
  ([`0b7717c`](https://github.com/synssins/sonorium/commit/0b7717c9611e1f179ab7e9d780715751b981efcc))


## v1.7.3-dev.34 (2026-10-13)

### Features

- **core**: Theme_edit.copy_link_in, the copy-in route delegates to it
  ([`aa58623`](https://github.com/synssins/sonorium/commit/aa5862322fa0df2a228022e1ebee30819708c0a6))


## v1.7.3-dev.32 (2026-10-13)

### Bug Fixes

- **plugins**: Nothing a plugin declares runs or installs before approval
  ([`b21516e`](https://github.com/synssins/sonorium/commit/b21516e47484ba9d1c76a7747b7b9a945a751708))


## v1.7.3-dev.30 (2026-10-13)

### Features

- **plugins**: Automation pieces: when, only if, do in command maps
  ([`4a97681`](https://github.com/synssins/sonorium/commit/4a9768169158e6d8ce82ba0fa8f47e20c4bd8719))

- **plugins**: Permissions, a plugin's own routes, Home Assistant entities
  ([`b735ee4`](https://github.com/synssins/sonorium/commit/b735ee44477d9c5c07c6eb20b484b44c5abb6c45))


## v1.7.3-dev.26 (2026-10-13)

### Features

- **api**: Capabilities list in GET /api/info
  ([`44f6aef`](https://github.com/synssins/sonorium/commit/44f6aef1cee0c17e6a304570c074c913f49902cb))
## v1.7.3-dev.23 (2026-10-13)

### Features

- **plugins**: The cue timeline: which sound is heard where and when
  ([`dbc7ada`](https://github.com/synssins/sonorium/commit/dbc7ada177fe84c3f37f4b91a8b75246a5d1e9a2))


## v1.7.3-dev.22 (2026-10-13)

### Bug Fixes

- **ui**: Phone header keeps one row; menu toggles in groups; entries keep their values
  ([`8c9823c`](https://github.com/synssins/sonorium/commit/8c9823c8c9f25a03954167f5713798ad224daa07))

### Features

- **plugins**: Attributes on every object a screen shows
  ([`7781fde`](https://github.com/synssins/sonorium/commit/7781fde0032cf48501e40230a6892418c86862c9))


## v1.7.3-dev.21 (2026-10-13)

### Bug Fixes

- **playback**: A group's file that ends cut off fades out instead of stopping dead
  ([`ac10c4a`](https://github.com/synssins/sonorium/commit/ac10c4a31db13ddd6bbd298c00ed6604fd97ff4f))

- **web**: Link App dialog closes itself when its code links a phone or expires
  ([`084e63f`](https://github.com/synssins/sonorium/commit/084e63f9f496ef0c4ad6d7759994ee3ab2540fcb))

### Features

- **core**: Theme details, preset import, step tracks, file operations, load without writing
  ([`3f0831a`](https://github.com/synssins/sonorium/commit/3f0831adb7c58e9a43341f80e9184876e43de316))
## v1.7.3-dev.16 (2026-10-13)

### Bug Fixes

- **ui**: No ownership wording in the spots' positions and messages
  ([`5ede97b`](https://github.com/synssins/sonorium/commit/5ede97bf9b8ea7b5c11152641f5570739c0fbef4))

### Documentation

- Adding to any screen (PLUGIN_DEVELOPMENT 24), extensible screens (UI_STYLE)
  ([`a0c5b43`](https://github.com/synssins/sonorium/commit/a0c5b43f6b570b90e1c55bf84e244961a0da6dc9))

### Features

- **plugins**: UI contributions with a contract for every open spot
  ([`80702e0`](https://github.com/synssins/sonorium/commit/80702e0098c552be7aae21b1a733b20f0175b524))

- **ui**: One registry draws every screen's open spots
  ([`87a9b93`](https://github.com/synssins/sonorium/commit/87a9b936ae51b30a590b8f9d99fe28388ee01856))

### Testing

- **ui**: A plugin in every spot, contracts refused per spot, the DOM check
  ([`9c8b455`](https://github.com/synssins/sonorium/commit/9c8b455f6c7231c50e53c96e43841d823d1fe483))


## v1.7.3-dev.15 (2026-10-12)

### Bug Fixes

- **plugins**: Plugin API stays 1.2; attributes are part of 1.2
  ([`9bbc136`](https://github.com/synssins/sonorium/commit/9bbc13637bc26788775749f41ca22fcddf673183))


## v1.7.3-dev.13 (2026-10-12)

### Documentation

- Plugin attributes and Sync (PLUGIN_DEVELOPMENT 23, THEME_FORMAT 2.7)
  ([`5738c13`](https://github.com/synssins/sonorium/commit/5738c13bc80d8381acc0ab0aff5d2ef46bdbf583))

### Features

- **plugins**: Declared plugin attributes on core objects, per-speaker Sync
  ([`0ac7925`](https://github.com/synssins/sonorium/commit/0ac7925efb4dab2feaae4ab9bcc1902bf0aaf00d))


## v1.7.3-dev.11 (2026-10-12)


## v1.7.3-dev.10 (2026-10-12)

### Documentation

- **plugins**: Audio outputs as speakers, API 1.2
  ([`09bf09f`](https://github.com/synssins/sonorium/commit/09bf09f0abbc23643fec9f39300e15b8cca72554))

### Features

- **plugins**: Audio output devices are speakers (plugin API 1.2)
  ([`14f4dba`](https://github.com/synssins/sonorium/commit/14f4dba94d4cb0ec1a89b41617c09525de3bd127))


## v1.7.3-dev.8 (2026-10-12)

### Bug Fixes

- **plugins**: A refused re-upload keeps the installed plugin
  ([`454681b`](https://github.com/synssins/sonorium/commit/454681b026bff3c3d6b2c7b0596d2dc88d619179))

- **plugins**: Audio-output routes answer a plugin's errors, never a bare 500
  ([`db619c9`](https://github.com/synssins/sonorium/commit/db619c94cd20679fc9a9e0951ab0e3caf9b28621))

- **plugins**: Uploaded zips keep subfolders and accept backslash names
  ([`5016994`](https://github.com/synssins/sonorium/commit/50169942fbf94361f2ab595088e6bd0164848036))

### Documentation

- **engine**: Reload_theme and the theme editing, playlist and network speaker modules
  ([`db7fb67`](https://github.com/synssins/sonorium/commit/db7fb670daf18e0c5a667b6ac72655a06b0f9613))

### Features

- **engine**: Engine.reload_theme() follows edits saved to the theme's files
  ([`0098275`](https://github.com/synssins/sonorium/commit/009827511ac3d42702d4dbb1178b1d07dbcb5fec))

### Refactoring

- **core**: Theme Editor, sequence and playlist logic as plain modules
  ([`ddb12a0`](https://github.com/synssins/sonorium/commit/ddb12a0a9160a5b7a75be2f2a35e0c9b18679c48))


## v1.7.3-dev.4 (2026-10-12)

### Bug Fixes

- **plugins**: Say why a plugin didn't load or switch on, and name it on upload
  ([`8e06e49`](https://github.com/synssins/sonorium/commit/8e06e49b6da4a01ab5dfa87c9a0f1f4ee683dc06))


## v1.7.3-dev.3 (2026-10-12)

### Features

- **engine**: Sequences, playlists and theme crossfades in the standalone engine
  ([`c7f4627`](https://github.com/synssins/sonorium/commit/c7f4627d967933bf2181b0c8f31a7bb775e58bc7))


## v1.7.3-dev.2 (2026-10-12)

### Bug Fixes

- **auth**: A key's Last used counts only accepted requests
  ([`8dedaea`](https://github.com/synssins/sonorium/commit/8dedaeafb9f861cf6e0f6bbe302ea21d2a56683f))

- **core**: Channels playing before a requested restart play again after it
  ([`06ffc9d`](https://github.com/synssins/sonorium/commit/06ffc9d98e1ae993099ff0928d72b38b35b1bfe8))

- **core**: Refuse starting a channel beyond Max channels
  ([`e102145`](https://github.com/synssins/sonorium/commit/e10214529ac92019fba7a38b113f32ddf38172f6))

- **mqtt**: Removing MQTT removes the Announce and plugin entities from Home Assistant
  ([`bbca594`](https://github.com/synssins/sonorium/commit/bbca59401eff5f6f49f5bf504507feed61bc4801))

- **settings**: Drop the unused entity_prefix setting
  ([`fb6aa34`](https://github.com/synssins/sonorium/commit/fb6aa342c66b9a144a56f8d95236bb90ada56ac9))

- **web**: Channel card keeps its Kid mode and owner marks and the countdown visible
  ([`fad48ca`](https://github.com/synssins/sonorium/commit/fad48ca4dcb921958c13d8cbc3af1466564a3af6))

- **web**: Esc cancels the Theme Editor and closes the channel editor
  ([`2be3127`](https://github.com/synssins/sonorium/commit/2be312740b854a248bce08c69351f1096febd162))

- **web**: Help points to Settings > Advanced for the stream address
  ([`98764d7`](https://github.com/synssins/sonorium/commit/98764d7b116a6e48e1bba725283f2e3239e6f23f))

- **web**: New Channel starts at the Audio Settings default volume
  ([`4866959`](https://github.com/synssins/sonorium/commit/4866959048d9eb0d1b7b5d9f4908a18d8156d9d4))

### Continuous Integration

- **release**: Dev release notes list the changes when the changelog section is empty
  ([`435cf71`](https://github.com/synssins/sonorium/commit/435cf7102035c7045fbb55d56ea3d0a38a538109))

### Documentation

- **readme**: List the fixes in Next release
  ([`653dfd2`](https://github.com/synssins/sonorium/commit/653dfd226707a24d7902c83a60beedccb53233fe))

### Testing

- **zones**: Fake channel list answers the Max channels check without using up a channel
  ([`952147f`](https://github.com/synssins/sonorium/commit/952147f971b491e886ca2952ef4c1543ecb04050))


## v1.7.2-dev.5 (2026-10-12)


## v1.7.2-dev.3 (2026-10-12)


## v1.7.1-dev.12 (2026-10-12)
## v1.7.2-dev.2 (2026-10-12)

### Bug Fixes

- **import**: A theme zip without a top folder keeps its groups
  ([`daa08c9`](https://github.com/synssins/sonorium/commit/daa08c9bfd171048f56ea1a560d53c0814645ac1))

- **presets**: A preset's and a While playing step's Rise reach the channel
  ([`1d3f74a`](https://github.com/synssins/sonorium/commit/1d3f74ae9777e56263580b8e148d01ca65c7ed41))

- **sequences**: Check hand-written sequence values when presets.json is read
  ([`d20ba9d`](https://github.com/synssins/sonorium/commit/d20ba9d38810e7966e845c879f69455e9104c3de))

- **themes**: Convert Ebb & Flow Interval to Duration only for themes from before the change
  ([`063dfab`](https://github.com/synssins/sonorium/commit/063dfabd3c5caf0e7a6ab7b030f81f8427792d2f))

### Continuous Integration

- **windows**: Documentation screenshots [screens]
  ([`eb08cf2`](https://github.com/synssins/sonorium/commit/eb08cf29a6acc5226e2a4e7ebd88b4ad7455e73e))

- **windows**: Documentation screenshots never run inside a release
  ([`a9faa8a`](https://github.com/synssins/sonorium/commit/a9faa8a341b44d5804cd027a9c298f640c9abe30))


## v1.7.1-dev.11 (2026-10-12)

### Bug Fixes

- **windows**: The installer follows Windows' dark mode; screenshot script clicks command links
  [screens]
  ([`f7e325f`](https://github.com/synssins/sonorium/commit/f7e325f61bcb0ef15f1129092d2c940f963d6b57))

### Chores

- Retire the old Windows app
  ([`9c95dc6`](https://github.com/synssins/sonorium/commit/9c95dc609ff73b8cd7b4fa29046956adebce1a8b))

### Continuous Integration

- **windows**: Documentation screenshots on a runner [screens]
  ([`5c849ec`](https://github.com/synssins/sonorium/commit/5c849ec43b4b7db96bab27052399ee7775d467a6))

### Documentation

- **versioning**: The old Windows app's v* tags are gone
  ([`b18a22e`](https://github.com/synssins/sonorium/commit/b18a22e1151f325270e4fc31f606a97085dc4c6d))


## v1.7.1-dev.10 (2026-10-12)

### Bug Fixes

- **windows**: Help opens the documentation (sonorium.app/docs.html)
  ([`79c74a2`](https://github.com/synssins/sonorium/commit/79c74a2830aa59ee81112bd0cf08bbb73847cfca))

### Continuous Integration

- Build the Windows installer from the add-on's tag and release it
  ([`26313e2`](https://github.com/synssins/sonorium/commit/26313e28f9557bd9c472dded8b12049c4a646a6b))

### Documentation

- **network**: Drop references to the retired Windows app
  ([`ebd9d08`](https://github.com/synssins/sonorium/commit/ebd9d08edf31a9ce1612a6d450a8df20bf69624f))


## v1.7.1-dev.9 (2026-10-12)

### Bug Fixes

- **security**: Guard the other exact-match theme lookup too (as in 1.7.1)
  ([`0236d4c`](https://github.com/synssins/sonorium/commit/0236d4ceab46312104a77ebf5f6cafd9be562c18))


## v1.7.1-dev.8 (2026-10-12)

### Bug Fixes

- **runtime**: No warning when the secret helper simply has nothing stored
  ([`66c4a78`](https://github.com/synssins/sonorium/commit/66c4a787f8c8f40b5c5a76b3482dd02009ab4c48))


## v1.7.1-dev.7 (2026-10-12)


## v1.7.1-dev.5 (2026-10-12)


## v1.7.1-dev.4 (2026-10-12)

### Features

- **api**: /api/info started_at; ci: FFmpeg licence check also reads the configure flags
  ([`cba56c9`](https://github.com/synssins/sonorium/commit/cba56c9676b00009fea5b81ee000edebb76e9570))
## v1.7.1-dev.3 (2026-10-12)

### Bug Fixes

- Console credential tests and feature docs after merging fixes
  ([`c24f090`](https://github.com/synssins/sonorium/commit/c24f090bd06fb1ffa0ea28ac1b8296fd3407a65f))

### Documentation

- Users and Zones are part of Sonorium, not a plugin
  ([`d45af0e`](https://github.com/synssins/sonorium/commit/d45af0e31074d28dd31cc8c38a454d2031d77b08))

### Features

- Users (accounts, sign-in, Zones) are part of Sonorium, not a plugin
  ([`5fe0125`](https://github.com/synssins/sonorium/commit/5fe01252de62edd4e7c52cc8e1a91a2b1cb26f9f))


## v1.7.1-dev.2 (2026-10-12)

### Documentation

- HOSTING.md for the host switches, exit code 75 and the console credential
  ([`bd844b2`](https://github.com/synssins/sonorium/commit/bd844b220b64ec8d1240b4027dd45c79544026ca))


## v1.6.2-dev.68 (2026-10-12)

### Bug Fixes

- Theme files use the configured audio folder, not /media/sonorium
  ([`27fdda7`](https://github.com/synssins/sonorium/commit/27fdda7948c9e6fc98c5d5209e3d2b9fce38fc16))


## v1.6.2-dev.67 (2026-10-12)


## v1.6.2-dev.65 (2026-10-12)


## v1.6.2-dev.63 (2026-10-12)

### Bug Fixes

- Read text files as UTF-8 everywhere (themes failed on Windows)
  ([`ce8e21c`](https://github.com/synssins/sonorium/commit/ce8e21cab78c19615bcd0b0153c8a011cd3352b1))


## v1.6.2-dev.62 (2026-10-12)

### Features

- **engine**: Standalone playback engine with golden render
  ([`95d4b38`](https://github.com/synssins/sonorium/commit/95d4b38434271b4289af26b2bb9df001aaf60814))

### Refactoring

- **playback**: Decoder seam, scoped randomness, lighter imports
  ([`1d49788`](https://github.com/synssins/sonorium/commit/1d497885c4c6bf86eca532af07a991b77fdf5829))
## v1.6.2-dev.64 (2026-10-12)

### Features

- Personal channel per account and live events for playlists, groups, spaces, settings
  ([`c8dac02`](https://github.com/synssins/sonorium/commit/c8dac025da8562147654a8fec32673fb72217b67))
## v1.6.2-dev.66 (2026-10-12)

### Continuous Integration

- **addon**: Fetch the branch head before moving the branch
  ([`d0da05c`](https://github.com/synssins/sonorium/commit/d0da05c712d701e76ccfeda45c2c9c63e9213ef2))

- **addon**: Push add-on images before the version commit; HA pulls them
  ([`6baf5b9`](https://github.com/synssins/sonorium/commit/6baf5b93e20221a9a7fb02b6ff4de5bc237aef29))

- **addon**: Reserve the version tag before building the add-on images
  ([`584e34c`](https://github.com/synssins/sonorium/commit/584e34cb57b779dd65c8bbb0fddf034d4363aae5))

### Documentation

- **app-api**: Documentation addresses instead of LAN addresses
  ([`15a7458`](https://github.com/synssins/sonorium/commit/15a74581cb874b079a1fc5e869aae55affdef9cc))


## v1.6.2-dev.61 (2026-10-12)


## v1.6.2-dev.60 (2026-10-12)


## v1.6.2-dev.59 (2026-10-12)


## v1.6.2-dev.57 (2026-10-12)


## v1.6.2-dev.55 (2026-10-12)

### Continuous Integration

- Only fixes publishes :dev; Users 1.1.3: no 'open to anyone' warning while plugins reload
  ([`151fd49`](https://github.com/synssins/sonorium/commit/151fd49dcc83e1251c8cbe8acb8b7ce89ec3e6d5))
## v1.6.2-dev.56 (2026-10-12)

### Bug Fixes

- **announce**: An unknown channel's message keeps its quotes; the chime-only notice doesn't ask for
  a chime
  ([`664b2b5`](https://github.com/synssins/sonorium/commit/664b2b5de455005033c1eff82fc222aabd5447f6))

- **web**: The channel editor's Announcements switch lines up with its label
  ([`ca79ae7`](https://github.com/synssins/sonorium/commit/ca79ae78174d0149dccf7ad4cb6b4f738f49a061))

### Documentation

- Announcements: let other systems use Sonorium (HA, key + curl, MQTT, Node-RED)
  ([`74eb5c9`](https://github.com/synssins/sonorium/commit/74eb5c9b5c2db2d1f0d080a08b2ff24dfa143093))

### Features

- **announce**: Other systems announce through Sonorium (HTTP, MQTT, Home Assistant)
  ([`de2bb61`](https://github.com/synssins/sonorium/commit/de2bb6125257eb1175f171560edbeef320418a6c))

- **channels**: Announcements on/off per channel (default on)
  ([`9bc9e6a`](https://github.com/synssins/sonorium/commit/9bc9e6a17a24f85cb83b6ee1eec12b6315acc1d0))

### Refactoring

- Remove notification rules, notifiers and Settings › Notifications
  ([`1e83a4f`](https://github.com/synssins/sonorium/commit/1e83a4fe4408875584f1d4c5ad942ee5f7846031))


## v1.6.2-dev.53 (2026-10-12)


## v1.6.2-dev.52 (2026-10-12)

### Bug Fixes

- **web**: Advanced › Stream URL's default isn't covered by its field on phones
  ([`79f7a61`](https://github.com/synssins/sonorium/commit/79f7a619a16395ddc7a7d7fbfc45e9270072b76f))


## v1.6.2-dev.50 (2026-10-12)

### Bug Fixes

- **plugins**: Sign-in stays closed while plugins reload
  ([`03b3d70`](https://github.com/synssins/sonorium/commit/03b3d708920208735065abaaca8b6d82b2d43755))

- **users**: Unlinked_devices lists only phones without a user (Users 1.1.1)
  ([`605c882`](https://github.com/synssins/sonorium/commit/605c8822ad5c47bae08c3b7a82d4369a015c7cf9))
## v1.6.2-dev.51 (2026-10-12)

### Features

- **ui**: Screen-size layout (approved boards)
  ([`abd591f`](https://github.com/synssins/sonorium/commit/abd591faef64fd726b734e0a9d05f866e40b70be))


## v1.6.2-dev.49 (2026-10-12)


## v1.6.2-dev.48 (2026-10-12)

### Features

- **web**: System › Servers in the Sonorium app (SONORIUM_HOST.showServers)
  ([`020a907`](https://github.com/synssins/sonorium/commit/020a90774408d336b722325c4d2b146bcad1fbd0))


## v1.6.2-dev.47 (2026-10-12)

### Bug Fixes

- **web**: No dashboard-cards line without Home Assistant; Advanced tables share a column
  ([`ebd9b59`](https://github.com/synssins/sonorium/commit/ebd9b59cf6f368be6de8576d3110b5908e3dcd8a))


## v1.6.2-dev.46 (2026-10-12)

### Continuous Integration

- First build from the new repository
  ([`532d8f9`](https://github.com/synssins/sonorium/commit/532d8f92a520131d8bc34caabb494f51483f7337))


## v1.6.2-dev.45 (2026-10-12)


## v1.6.2-dev.43 (2026-10-11)


## v1.6.2-dev.42 (2026-10-11)


## v1.6.2-dev.39 (2026-10-11)


## v1.6.2-dev.37 (2026-10-11)

### Build System

- Audio-only LGPL FFmpeg and PyAV built from source in both images
  ([`8259b23`](https://github.com/synssins/sonorium/commit/8259b231e6e09a830cc1d9eeee37b20ef1b7c697))

### Continuous Integration

- Publish prebuilt add-on images per architecture; check FFmpeg is LGPL
  ([`778d6ca`](https://github.com/synssins/sonorium/commit/778d6cafa9d797e9a5a59a1bdbc52fd94da384fa))

### Documentation

- **notices**: Sonorium's own LGPL FFmpeg build replaces PyAV's bundled libraries
  ([`47eb21d`](https://github.com/synssins/sonorium/commit/47eb21dfe8d66fd1d1159dd2c132e3378c5b3102))

### Testing

- Audio formats test runs inside the images
  ([`98efb90`](https://github.com/synssins/sonorium/commit/98efb9069519675bbbc20222bf388f99a0d4da50))
## v1.6.2-dev.41 (2026-10-11)

### Documentation

- Zones, the channel policy and the new UI slots
  ([`5718e0b`](https://github.com/synssins/sonorium/commit/5718e0b1f9a21d3c1c4e76506eed7e62533a0ad5))

### Features

- **web**: Zones in the web UI
  ([`46d0bf0`](https://github.com/synssins/sonorium/commit/46d0bf08fdbfdc4d7060ee0eee088a486f61f600))


## v1.6.2-dev.38 (2026-10-11)

### Bug Fixes

- **core**: Channel policy hears of new channels; load without it
  ([`081eae8`](https://github.com/synssins/sonorium/commit/081eae89055f841bf6c569f20ab37a6e1bab6269))

### Features

- **core**: Channel policy hook for zones
  ([`3144f2d`](https://github.com/synssins/sonorium/commit/3144f2d337b23723d4ce406cc5320732f2aa4007))

- **users**: Zones, Kid mode, speaker homes and links
  ([`0ebf819`](https://github.com/synssins/sonorium/commit/0ebf819ee3042ae721d9ff351e32da6dab0eddaf))
## v1.6.2-dev.44 (2026-10-11)


## v1.6.2-dev.40 (2026-10-11)


## v1.6.2-dev.35 (2026-10-11)


## v1.6.2-dev.33 (2026-10-11)

### Bug Fixes

- **users**: Plugin 1.0.3, so installs pick up the Home Assistant user list from HA's WebSocket
  ([`d211a54`](https://github.com/synssins/sonorium/commit/d211a545a322d88fbf03187d62534cdd4080571c))


## v1.6.2-dev.32 (2026-10-11)


## v1.6.2-dev.30 (2026-10-11)


## v1.6.2-dev.29 (2026-10-11)

### Bug Fixes

- **plugins**: An uninstalled built-in can be installed again from the Catalog; Update only for a
  newer version
  ([`768803b`](https://github.com/synssins/sonorium/commit/768803b1593ab9a68923f54b74534f3703e5343b))


## v1.6.2-dev.28 (2026-10-11)


## v1.6.2-dev.27 (2026-10-11)

### Documentation

- Home Assistant's users on the add-on (AUTH, APP_API, add-on README)
  ([`50e051a`](https://github.com/synssins/sonorium/commit/50e051adb287e2caf81ae762cc2b4f077f4ddf95))

- Next release in What's new, Sign-in section, HTTPS port in the compose example,
  THIRD_PARTY_NOTICES.md
  ([`5833f46`](https://github.com/synssins/sonorium/commit/5833f46d06e011c013f5a85ad36736856bdfab28))


## v1.6.2-dev.26 (2026-10-11)


## v1.6.2-dev.25 (2026-10-11)

### Bug Fixes

- **status**: Ebb & Flow counts down to the fade at the file's end; theme's own name
  ([`cec35a9`](https://github.com/synssins/sonorium/commit/cec35a99cef8aaa8f9a837692d5c5d4f64287ada))

### Documentation

- APP_API.md, the API reference for the Android app
  ([`e749dd2`](https://github.com/synssins/sonorium/commit/e749dd2f98b7b3912ac91639bf22c681c5deb7ec))

- Now playing board and GET /api/now-playing
  ([`e33c340`](https://github.com/synssins/sonorium/commit/e33c340dc4abe0eeaebddb77e7b6ee6938dbfd0f))

### Features

- **status**: GET /api/now-playing, the mixer's live track state
  ([`0a1cbc6`](https://github.com/synssins/sonorium/commit/0a1cbc6bae6d5a8e01b96274e81beef72c10e12c))

- **status**: Now playing on the Status page
  ([`03fc89e`](https://github.com/synssins/sonorium/commit/03fc89e34c1b7651a449134afaa21a033b6dd96f))


## v1.6.2-dev.24 (2026-10-11)

### Bug Fixes

- **auth**: Skip container and VPN bridges (incus, LXD, LXC, Tailscale, ZeroTier, WireGuard) in the
  QR and mDNS addresses
  ([`ac2819b`](https://github.com/synssins/sonorium/commit/ac2819b22a5043f60f865683616dad8364e8b132))


## v1.6.2-dev.23 (2026-10-11)


## v1.6.2-dev.21 (2026-10-11)


## v1.6.2-dev.20 (2026-10-11)

### Continuous Integration

- Smoke test checks the public /api/info
  ([`d1fe367`](https://github.com/synssins/sonorium/commit/d1fe3675b5e0675c3ad99f2e88f9854da495d640))


## v1.6.2-dev.19 (2026-10-11)


## v1.6.2-dev.18 (2026-10-11)

### Build System

- **auth**: Add-on ingress port 8099, HTTPS port 8808, sign-in options and packages
  ([`2d64e32`](https://github.com/synssins/sonorium/commit/2d64e329214d0e84a231c83ffb7fed1bf865431f))

### Documentation

- **auth**: Sign-in, linked phones and keys; plugin sign-in hooks and web UI slots
  ([`e02465b`](https://github.com/synssins/sonorium/commit/e02465b2c49ce37ff7865641fef9d1206fc58baa))

### Features

- **plugins**: Built-in Users and Integration plugins
  ([`5f67896`](https://github.com/synssins/sonorium/commit/5f678967c6c1326b20b8b8e357c84654149f93b2))

- **ui**: Web UI slots for built-in plugins, Link App, sign-in screens
  ([`5151f5b`](https://github.com/synssins/sonorium/commit/5151f5b690ddb4e3a7cca9cdd254cb3ea99d9b37))

### Refactoring

- **auth**: Core with authenticator hooks; accounts and keys leave the core
  ([`ce24ef3`](https://github.com/synssins/sonorium/commit/ce24ef3aca6a2f6f6a4d20730c878d7fbe213d25))

- **auth**: One token service, secrets file and rate limiter in core
  ([`9e87bca`](https://github.com/synssins/sonorium/commit/9e87bca2db17e645f68a4cf394e412072f25658a))

### Testing

- **auth**: Sign-in, roles, pairing, recovery codes, keys, bypass, ingress, route coverage
  ([`a6b9520`](https://github.com/synssins/sonorium/commit/a6b9520aa7283a7848c53b945acc31f900351788))


## v1.6.2-dev.17 (2026-10-11)

### Features

- **auth**: Accounts, sessions, devices, keys and route permissions (backend, WIP)
  ([`6fa2437`](https://github.com/synssins/sonorium/commit/6fa24379ed39b3dbfc80ae31ad92f262249a8911))

- **auth**: Wire sign-in into the app (middleware, router, HTTPS and ingress listeners)
  ([`49a2ee5`](https://github.com/synssins/sonorium/commit/49a2ee5961222fbdc2c3789f9238dbb63883dc9d))


## v1.6.2-dev.16 (2026-10-11)


## v1.6.2-dev.15 (2026-10-11)

### Documentation

- **plugins**: Plugin development guide
  ([`a03659e`](https://github.com/synssins/sonorium/commit/a03659ec50578db018af7b1c39a23bc0ede2cf8d))

### Features

- **plugins**: Event bus with a public event catalogue
  ([`f938635`](https://github.com/synssins/sonorium/commit/f938635f1f829287114784855ab9f0a4db2dd47d))

- **plugins**: Framework endpoints, start-up wiring and example plugin
  ([`b3d0c5d`](https://github.com/synssins/sonorium/commit/b3d0c5d4abd673d80de0e3d149f74735fadd1284))

- **plugins**: Persistent scheduler service
  ([`26f55ea`](https://github.com/synssins/sonorium/commit/26f55ea611aaeb3e7ae2a97d5031f26ab354e0af))

- **plugins**: Plugin host with actions API, triggers, notifications, extension points
  ([`9b12b75`](https://github.com/synssins/sonorium/commit/9b12b75dcc0b406982a078e9c40ec42a5deb3f40))


## v1.6.2-dev.14 (2026-10-11)


## v1.6.2-dev.13 (2026-10-11)


## v1.6.2-dev.12 (2026-10-11)


## v1.6.2-dev.10 (2026-10-11)


## v1.6.2-dev.9 (2026-10-11)

### Features

- **ambient-mixer**: Random channels' presence follows the global Wait between plays (3.1.2)
  ([`2244e02`](https://github.com/synssins/sonorium/commit/2244e021ccbe6e3788c900b83cb3a427324e8173))

- **playback**: Global playback values (Settings > Advanced) read live by the mixer
  ([`6bf1b16`](https://github.com/synssins/sonorium/commit/6bf1b16a61e4acfbb7c76ab00ed4f4547fc9ad6e))

- **ui**: Settings > Advanced shows the playback values, always in the menu
  ([`b377acd`](https://github.com/synssins/sonorium/commit/b377acdd5e84af61a816b8836be51a741fc9bf10))

### Testing

- **playback**: Playback values' defaults, persistence, API checks and the mixer following a change
  ([`b2ad55d`](https://github.com/synssins/sonorium/commit/b2ad55de102b28979fc7b565a6a5c881126dacba))


## v1.6.2-dev.8 (2026-10-11)

### Bug Fixes

- **ambient-mixer**: A mix named from its address reads "Spring Rain - Sanctuary" (3.1.1)
  ([`7f93a08`](https://github.com/synssins/sonorium/commit/7f93a0898131b7422ce6d18e792fdf28670cd722))

### Features

- **plugins**: Each plugin's settings under its form, saved as they're changed
  ([`47ecf8e`](https://github.com/synssins/sonorium/commit/47ecf8ec23c7b5b4536fc581ab35e83752ac74f8))


## v1.6.2-dev.7 (2026-10-11)

### Bug Fixes

- **ambient-mixer**: An edit saved during the download is no longer lost
  ([`87565d0`](https://github.com/synssins/sonorium/commit/87565d015bfa8038e773891f8fa1cb09c13a82e4))

- **api**: A replaced theme rescan no longer fails the plugin action
  ([`22f6551`](https://github.com/synssins/sonorium/commit/22f65512d429b1eb3c3ea9e651b16b97156be7ed))

- **api**: Plugin ids can't reach outside the plugins folder
  ([`0a83f71`](https://github.com/synssins/sonorium/commit/0a83f71d5219ddaafbec34d733ba5187c016407c))

- **plugins**: Built-in plugins can be uninstalled like any other
  ([`bd6a645`](https://github.com/synssins/sonorium/commit/bd6a645d8aa4de553a75647ff5d8530fb3a42fc7))

- **plugins**: Replace an installed built-in only when both versions are semver
  ([`af76e31`](https://github.com/synssins/sonorium/commit/af76e31efe45df4043c2c2eebc96260569cd33c2))

- **themes**: Hidden folders are never themes; leftovers of a crash are removed
  ([`debf12a`](https://github.com/synssins/sonorium/commit/debf12ac50e248aac11f7d931ad4b6f170a9d2be))


## v1.6.2-dev.5 (2026-10-11)

### Bug Fixes

- **ambient-mixer**: Real playback modes, gapless loop and theme picked by id (3.1.0)
  ([`407d9f4`](https://github.com/synssins/sonorium/commit/407d9f4f70e1a02cb88a6e0bc01bab47c14a8a40))

- **plugins**: Find_theme compares ids and names only, so symlinked theme folders work
  ([`4722c13`](https://github.com/synssins/sonorium/commit/4722c1354c18f913dcfa6b385aa34eb2db024863))

- **plugins**: Mark the bundled plugins as built-in
  ([`38baa61`](https://github.com/synssins/sonorium/commit/38baa614c235e075dd3ae9bf20f5a77bbb027245))

### Chores

- **ambient-mixer**: Add the package marker the downloadable zip has
  ([`98f58e6`](https://github.com/synssins/sonorium/commit/98f58e67cf94c2cd35743c8bb3e6971dffe7f3f5))

- **plugins**: Rebuild downloadable zips for Ambient Mixer 3.1.0 and Theme Merge 2.0.0
  ([`9a1266e`](https://github.com/synssins/sonorium/commit/9a1266ebfabc6e08698c463a7f783ffcd64c5879))

### Features

- **api**: Rescan themes after a plugin action and call the theme hooks
  ([`b4b03e6`](https://github.com/synssins/sonorium/commit/b4b03e6d6a760a193aa71bc7146a1df27a2fd057))

- **plugins**: Documented settings schema with validation and defaults
  ([`24e2497`](https://github.com/synssins/sonorium/commit/24e2497e3a27d0fa293d6e09166c0dd4f830bcc4))

- **plugins**: Theme helper can add files that aren't tracks
  ([`ee6ca24`](https://github.com/synssins/sonorium/commit/ee6ca2409a44f7fcd0ce2baee8dfbf9265d105da))

- **plugins**: Theme helper so plugins never write theme JSON themselves
  ([`90daa49`](https://github.com/synssins/sonorium/commit/90daa4951bb45af4361cbdd98abbd6104555f311))

- **plugins**: Update bundled plugins when the bundled version is newer
  ([`a0da8e0`](https://github.com/synssins/sonorium/commit/a0da8e0e390cdb3800697c96cdd98276f4b72b04))

- **theme-merge**: Rewrite for Themes 2.0 (2.0.0)
  ([`85d7cf1`](https://github.com/synssins/sonorium/commit/85d7cf1c94b27fc1f0f2453ac53ee42ce460ce07))

### Testing

- **ambient-mixer**: Mode mapping, seamless_loop and imports without network
  ([`7de4850`](https://github.com/synssins/sonorium/commit/7de485033f5167e2c7ae462504fb68e56b9834a0))

- **plugins**: Catalog zips match the bundled plugins
  ([`be1c23e`](https://github.com/synssins/sonorium/commit/be1c23e67d41fb93a635f617f6c956b931a79563))

- **plugins**: Settings schema, bundled updates, rescan after actions
  ([`4b2926a`](https://github.com/synssins/sonorium/commit/4b2926a0d7edaccaf8099f2abe3fc1ed9427489d))

- **plugins**: Theme helper writes clean 2.0 themes and rolls back
  ([`ed785e3`](https://github.com/synssins/sonorium/commit/ed785e3c863686cc2dc74dec850188230468d3e1))

- **theme-merge**: New theme, into either source, groups, links, presets
  ([`594d77f`](https://github.com/synssins/sonorium/commit/594d77f33b3ec75ed5574dbc30085f37b76ba516))


## v1.6.2-dev.4 (2026-10-11)


## v1.6.2-dev.3 (2026-10-11)

### Features

- **ui**: A sequence step's dot also lights for While playing changes
  ([`9986a37`](https://github.com/synssins/sonorium/commit/9986a3743124be2919b866c1863b4896de5133a3))


## v1.6.2-dev.2 (2026-10-11)

### Bug Fixes

- **ui**: Short file threshold removed from the Theme Editor's menu (it only fed Auto, which is
  retired)
  ([`383244d`](https://github.com/synssins/sonorium/commit/383244d4872571affc57f0174110d72b256da40e))

### Documentation

- Remove the writing style file
  ([`39f8b28`](https://github.com/synssins/sonorium/commit/39f8b28a7eae07c104042fc7f8b6a6c887bd7685))

- Writing style for the README, wiki, site and help
  ([`bacef35`](https://github.com/synssins/sonorium/commit/bacef35b62ba34d2ab41094a482d5a6fe4aca0dc))


## v1.6.2-dev.1 (2026-10-11)

### Documentation

- **changelog**: 1.6.1 notice and highlights [skip ci]
  ([`c99e0c6`](https://github.com/synssins/sonorium/commit/c99e0c69054ef378377bb29d32b5a35c57bb82a2))

- **ui**: The Live help line says what is heard at once (Preview mix, channels with no preset) and
  what waits for Save Preset
  ([`f8b1ecd`](https://github.com/synssins/sonorium/commit/f8b1ecd5c6df9663599e0db64f28a9b72178d48b))


## v1.6.1 (2026-10-11)

> ## ⭐ 1.6.1: Refinements
>
> Refinements across Sonorium, with the Theme Editor and Channels page restructured.

### Highlights

- **Sequences:** a theme's presets played one after another, gliding from one to the next, with per-step While playing changes and advanced transitions.
- **Playlists:** themes played one after another on a channel, in order or shuffled. Theme cycling became playlists.
- **Ebb & Flow:** tracks can be configured with a minimum and maximum volume, and will automatically move to any value between those two limits randomly for a period of time, controlled by Duration and Rise. Pause between plays; Ebb & Flow groups.
- **Theme Editor:** tracks listed in sections by playback mode (Unassigned, Background, Intermittent, Ebb & Flow). Auto and Intrusion groups retired; themes converted once, with backups.
- **Channels:** theme or playlist per channel, speaker mute, and listening in the browser with its own volume.
- **Playback:** file Fade in / Fade out, Gap saved in presets, smooth volume changes, preset glides, channels fade in and out.
- **Fixes:** saving a preset updates the channels playing it; a file added to a playing theme plays as soon as it has a Mode; Crossfade and Master output gain work in the Home Assistant app.

The full list is in the README under What's new.


## v1.6.1-dev.42 (2026-10-11)


## v1.6.1-dev.41 (2026-10-11)

### Documentation

- **ui**: Channels help: an unnamed channel is called Channel and a number
  ([`1aaa787`](https://github.com/synssins/sonorium/commit/1aaa7874ec29742b1cd346fc11b7ad56b418a2c4))


## v1.6.1-dev.40 (2026-10-11)

### Documentation

- Ebb & Flow described as minimum/maximum volume with Duration and Rise; breeze and gust
  screenshots; What's new without layout details
  ([`f74a70c`](https://github.com/synssins/sonorium/commit/f74a70c8c44a3cbb205a0fc423834558527e2e43))


## v1.6.1-dev.39 (2026-10-11)

### Documentation

- Theme Editor help screenshot
  ([`e131d53`](https://github.com/synssins/sonorium/commit/e131d534ae5a626b34d0595d926dd644d51cc3bb))

- **ui**: Help lines that restated their own control removed; the rest short and literal
  ([`263176f`](https://github.com/synssins/sonorium/commit/263176f28c9c6bbccaeba5f48b4b1138f15acbbb))


## v1.6.1-dev.38 (2026-10-11)

### Documentation

- Phone screenshot of the Channels page
  ([`18c4300`](https://github.com/synssins/sonorium/commit/18c4300619b48fb9d01db10715c5cbc1cb117746))

- **ui**: Theme Editor help in tiers, in the order the editor shows things; release named 1.6.1
  (refinements)
  ([`3d150b6`](https://github.com/synssins/sonorium/commit/3d150b682c696bd99164cce51e9004973c9bc121))


## v1.6.1-dev.37 (2026-10-11)

### Documentation

- 1.7.0 what's new and announcement, new screenshots, help copied into the READMEs; Channels board
  with mute and headphones rows; phone channel blocks fit three recent changes
  ([`2783d88`](https://github.com/synssins/sonorium/commit/2783d88a34cc1bf9b25a05d3771eb372f462ec87))


## v1.6.1-dev.36 (2026-10-11)

### Documentation

- **ui**: Help for the speaker and headphones buttons, Now and Next; Theme Editor help without Auto
  or intrusion groups, with Rise
  ([`d5ff290`](https://github.com/synssins/sonorium/commit/d5ff2905ed9012906b9491ee1c63beed29454190))


## v1.6.1-dev.35 (2026-10-11)

### Features

- **channels**: The speaker button mutes the speakers' stream (red, muted icon); browsers listening
  keep hearing it
  ([`8a2300f`](https://github.com/synssins/sonorium/commit/8a2300f15d7e5da1fa0fbcd673b10b9ff00600e4))


## v1.6.1-dev.34 (2026-10-11)

### Bug Fixes

- **channels**: One browser leaving keeps the mix for others; volume ramps stay in range; matching
  speaker and headphones boxes; listening fails quietly without audio
  ([`b36d3c6`](https://github.com/synssins/sonorium/commit/b36d3c69c057db52ab1d252029dcc27ce254ee6f))


## v1.6.1-dev.33 (2026-10-11)

### Code Style

- **channels**: The speaker icon centred over the headphones button
  ([`79cf029`](https://github.com/synssins/sonorium/commit/79cf029f746bc7bbd681c7f952def5088f48d8f4))


## v1.6.1-dev.32 (2026-10-10)


## v1.6.1-dev.31 (2026-10-10)


## v1.6.1-dev.29 (2026-10-10)


## v1.6.1-dev.28 (2026-10-10)

### Features

- **ui**: Channels page: Listen here button on each channel block
  ([`5e85480`](https://github.com/synssins/sonorium/commit/5e854801b6d1ec4451d4a7019458e4c7e8a7e5f0))


## v1.6.1-dev.27 (2026-10-10)

### Features

- **channels**: Listen here on the server: a channel's mix for browsers, apart from its speakers
  ([`a731f18`](https://github.com/synssins/sonorium/commit/a731f18cc935c4d3d28f65872dbbb47eb8346be8))


## v1.6.1-dev.25 (2026-10-10)


## v1.6.1-dev.24 (2026-10-10)

### Features

- **channels**: Keep each channel's recent changes and return them on the sessions API
  ([`555b348`](https://github.com/synssins/sonorium/commit/555b348299ab2b7cba64cd9cd2a27a6a4511f835))


## v1.6.1-dev.22 (2026-10-10)

### Bug Fixes

- **ui**: A track's menu moves it to Unassigned (not "Theme (no group)"); design board for the
  Channels page
  ([`930bca6`](https://github.com/synssins/sonorium/commit/930bca68ea05f17d10f74291cb3a64187502f2ca))


## v1.6.1-dev.21 (2026-10-10)

### Bug Fixes

- **ui**: A section's column headings use the section's colour, not the old grey header's
  ([`d41038a`](https://github.com/synssins/sonorium/commit/d41038a579b27484722be0e0f76b8cd07d7ca12e))


## v1.6.1-dev.20 (2026-10-10)


## v1.6.1-dev.19 (2026-10-10)

### Bug Fixes

- **theme load**: Auto conversion uses a 0 s short-file threshold as set
  ([`9a2d69b`](https://github.com/synssins/sonorium/commit/9a2d69bdd5038ec9615adf0842758bb69cedfceb))

### Documentation

- **theme format**: Linked files anywhere, intrusion groups and Auto retired, Unassigned
  ([`325b7eb`](https://github.com/synssins/sonorium/commit/325b7eb00f4d7a514a5f0e3f8bbc8f60c71a6b30))

### Testing

- Intrusion groups and Auto converted once with backups
  ([`a0547ee`](https://github.com/synssins/sonorium/commit/a0547eef1c2e158be3925f8aed96e02697a5a7a7))


## v1.6.1-dev.18 (2026-10-10)

### Documentation

- Approved design boards for sections, Ebb & Flow and a sequence step's While playing
  ([`b0197d1`](https://github.com/synssins/sonorium/commit/b0197d1eb5a5a6618178ab4f57f9b0f8bd2b22aa))

### Features

- Linked files anywhere; intrusion groups and Auto retired once on load; uploads start Unassigned
  ([`4c54025`](https://github.com/synssins/sonorium/commit/4c5402595a0c82da35a872fd23b3466c2d6e4e26))

- **groups**: Moved and renamed keys follow into sequences; names linked files hold stay free
  ([`b193769`](https://github.com/synssins/sonorium/commit/b193769564c79779e6c486b4e79c720aebec530f))


## v1.6.1-dev.14 (2026-10-10)

### Features

- Ebb & Flow Duration, drift and Rise; Unassigned mode; a sequence step's While playing layer
  ([`d3de5df`](https://github.com/synssins/sonorium/commit/d3de5dfaa487b1388f414263b46b3467fa1136b2))


## v1.6.1-dev.13 (2026-10-10)

### Bug Fixes

- Saving a preset updates the channels playing it
  ([`fd2fdb1`](https://github.com/synssins/sonorium/commit/fd2fdb17be9e3b3eca6a0301477d98c369fd60b6))


## v1.6.1-dev.12 (2026-10-10)

### Features

- Ebb & Flow groups, Pause between plays, two-knob volume; Interval sets how lively
  ([`863d3f6`](https://github.com/synssins/sonorium/commit/863d3f644699418ad19c67d499ae7d37978d5b81))


## v1.6.1-dev.11 (2026-10-10)

### Features

- Ebb & Flow rises and falls instead of coming and going
  ([`3acee12`](https://github.com/synssins/sonorium/commit/3acee12e9a726d30046dc597461b1731ad7d513f))


## v1.6.1-dev.10 (2026-10-10)

### Features

- Group gaps saved in presets; volume changes ease instead of stepping
  ([`6423fdd`](https://github.com/synssins/sonorium/commit/6423fdd1e6ec9de2626743c855a24e70f20590e2))


## v1.6.1-dev.9 (2026-10-10)

### Features

- File fades (Fade in / Fade out on a file); advanced transitions show every chosen style's setting
  ([`f05156f`](https://github.com/synssins/sonorium/commit/f05156f0fefd6032f49cb322bb9aedfe9721dd09))


## v1.6.1-dev.8 (2026-10-10)

### Bug Fixes

- The mix no longer ducks the moment a track starts fading in
  ([`5687652`](https://github.com/synssins/sonorium/commit/56876522b8915249cc0ea07f66c32095a7a0bc6c))

### Features

- Advanced transitions for sequence steps; playback log with a filter
  ([`121f2db`](https://github.com/synssins/sonorium/commit/121f2db11e54837068b37ca5b49a42e87e21b68f))


## v1.6.1-dev.7 (2026-10-10)

### Features

- Sequence steps fade tracks over the ramp; Ebb & Flow tracks start fresh
  ([`f2d8d6e`](https://github.com/synssins/sonorium/commit/f2d8d6e637860a7d0d5509bab4cc8767457d2515))


## v1.6.1-dev.6 (2026-10-10)

### Bug Fixes

- **ui**: Picking a preset in the Theme Editor loads it
  ([`3f314ba`](https://github.com/synssins/sonorium/commit/3f314badebdad0fd464f370f6afe5e62698b3544))


## v1.6.1-dev.5 (2026-10-10)

### Bug Fixes

- **ui**: Save theme sits next to Cancel in the Theme Editor
  ([`ed3e4ba`](https://github.com/synssins/sonorium/commit/ed3e4ba5dc171dd8e78e6e54d8cb5652bad6f0a4))


## v1.6.1-dev.4 (2026-10-10)

### Bug Fixes

- Shuffled playlists never repeat a theme back to back, nor A-B-A
  ([`810e6f6`](https://github.com/synssins/sonorium/commit/810e6f644a8cfde72373f09b5c2367050eb6e106))


## v1.6.1-dev.3 (2026-10-10)

### Features

- **ui**: Sequences and playlists
  ([`b43ac16`](https://github.com/synssins/sonorium/commit/b43ac168ce405a8f318dae918a2a6e6f80fbf536))


## v1.6.1-dev.2 (2026-10-10)

### Features

- Sequences are picked like presets; playlists can fit sequences into their time
  ([`9f96faa`](https://github.com/synssins/sonorium/commit/9f96faaa9367ee0f7fe80a9b5ee46d7dc7a1932c))


## v1.6.1-dev.1 (2026-10-10)

### Documentation

- **changelog**: 1.6.0 notice and highlights [skip ci]
  ([`dd7fcff`](https://github.com/synssins/sonorium/commit/dd7fcff5df3abd3fbeca4a151662cd187ff45da9))

### Features

- Sequences, playlists, preset glides and channel start/stop fades
  ([`d5a6dc5`](https://github.com/synssins/sonorium/commit/d5a6dc5b5701216a772ca47bd53a2f77e1da957b))


## v1.6.0 (2026-10-10)

> ## ⭐ 1.6.0: Merry-go-round and Intrusions
>
> Groups now have three modes. **Merry-go-round** turns a few recordings of the same scene into one continuous bed that crossfades from file to file and never repeats the same way. **Intrusion** lets a preset bring in sounds from another theme, like rain over a campsite, without copying any files. Tracks now fade in and out over 3 seconds when a preset turns them on or off.

### Highlights

- **Group modes:** Intermittent, **Merry-go-round** (a continuous bed that crossfades from file to file) and **Intrusion** (tracks keep their own settings and can be linked from other themes).
- **Preset marks:** ★ for the default, ⚭ for a preset that turns intrusion tracks on, in the Theme Editor, on channel cards and in the channel Edit dialog.
- **Smooth preset changes:** tracks fade in and out over 3 seconds when turned on or off.
- **Theme Editor:** instant moves, messages as small notices, track preview at the track's own volume.
- **Fixes:** new themes can be edited at once and keep the name you typed; Preview mix no longer shows a false error.
- **Known issue:** Crossfade and Master output gain (Settings → Audio Settings) are saved but don't change the sound yet.

The full list is in the README under What's new.

### Documentation

- 1.6.0 what's new and announcement; 1.5.2 and 1.5.0 kept under their own headings
  ([`a3f9c51`](https://github.com/synssins/sonorium/commit/a3f9c5175caf1ccccff8c483766dec91e8815bb3))


## v1.5.3-dev.9 (2026-10-10)

### Bug Fixes

- **ui**: The Edit dialog's preset list opens upward when there's no room below
  ([`6bc898f`](https://github.com/synssins/sonorium/commit/6bc898fa5eb58650b9ac3016b3c45ae1e514dc2c))


## v1.5.3-dev.8 (2026-10-10)

### Bug Fixes

- **ui**: Preset marks in the channel Edit dialog and the editor's preset list
  ([`3e33253`](https://github.com/synssins/sonorium/commit/3e33253442b90501dc69e684e6fd76d038e0a829))


## v1.5.3-dev.7 (2026-10-10)

### Bug Fixes

- A new theme can be edited at once and keeps the name you typed
  ([`ec81d2c`](https://github.com/synssins/sonorium/commit/ec81d2c5c45de4445b921441a85ce7ceabceff10))


## v1.5.3-dev.6 (2026-10-10)

### Documentation

- Intrusion mockup screenshots (desktop and phone)
  ([`f502223`](https://github.com/synssins/sonorium/commit/f502223ccc9b76505c85d3955b79f48de7013be5))

### Features

- **ui**: Intrusions in the Theme Editor and preset icons
  ([`0c190d1`](https://github.com/synssins/sonorium/commit/0c190d1400611d173cbb5346bff6cee198af7740))


## v1.5.3-dev.5 (2026-10-10)

### Bug Fixes

- **ui**: Preview mix no longer reports an error when it restarts
  ([`d976c69`](https://github.com/synssins/sonorium/commit/d976c694aee5417b0970fdd7967b2bf0aaa61201))

### Features

- Intrusion groups with files linked from other themes; 3 s fades on track on/off
  ([`96bce19`](https://github.com/synssins/sonorium/commit/96bce19d219116cdeda4ca1a170b5361ea702305))


## v1.5.3-dev.4 (2026-10-10)

### Bug Fixes

- **ui**: A track's preview plays at its volume and follows its slider
  ([`fadb858`](https://github.com/synssins/sonorium/commit/fadb85840014f7fbcefc3d56f1d5c038deed4664))


## v1.5.3-dev.3 (2026-10-10)

### Bug Fixes

- A new, empty theme can be edited, saved and given presets
  ([`9d0e710`](https://github.com/synssins/sonorium/commit/9d0e710131926fda9af5ce5a76fc69440571af24))


## v1.5.3-dev.2 (2026-10-10)

### Bug Fixes

- **ui**: Moving tracks is immediate; editor messages are toasts
  ([`af8f44b`](https://github.com/synssins/sonorium/commit/af8f44ba25631fe48dae9de830079fffb9ee8975))


## v1.5.3-dev.1 (2026-10-10)

### Features

- Merry-go-round group mode, a continuous crossfading bed
  ([`292250b`](https://github.com/synssins/sonorium/commit/292250b0c58d58135d9432be80d84d18786ac9d6))


## v1.5.2 (2026-10-10)

### Continuous Integration

- Release a patch when the computed version is already tagged
  ([`afcc9ea`](https://github.com/synssins/sonorium/commit/afcc9ea49793a6f7f3ba303fcfea34f2a981372e))

### Documentation

- Where to find the version and install type
  ([`e964bec`](https://github.com/synssins/sonorium/commit/e964bec844fae8ac598721ee053ed73a187f8e05))


## v1.5.1-dev.2 (2026-10-10)

### Documentation

- **changelog**: 1.5.0 major release notice and highlights [skip ci]
  ([`f441940`](https://github.com/synssins/sonorium/commit/f441940219568f5c1a3f54587ec8c7a458f6dfa5))

- **ui**: Help and READMEs explain Intermittent in groups and track weight
  ([`6252a24`](https://github.com/synssins/sonorium/commit/6252a246ab730a30db8118ac8ff143207f3a562e))


## v1.5.1-dev.1 (2026-10-10)

### Testing

- Skip the connection-removal tests where FastAPI isn't installed
  ([`06736c0`](https://github.com/synssins/sonorium/commit/06736c0466662b45b2eb611f090fdf8d941b01f8))


## v1.5.0 (2026-10-10)

> ## ⭐ Major Release: 1.5.0, Themes 2.0
>
> This is a major update. Themes can now hold **groups**, channels playing the same theme can each use their **own preset**, the **Theme Editor**, the **Themes** page and every **Settings** page are rebuilt, and the playback modes have **new names**.
>
> Before, two channels playing the same theme shared one preset: change it on one, and it changed on both. Now each channel is isolated.
>
> Your themes are converted automatically the first time this version starts. Each theme's old settings file is kept next to it as `metadata.json.pre-presets.bak`.

### Highlights

- **Groups:** tracks in a theme folder take turns, never overlap and play start to finish. The group picks the next track, and the same track never plays twice in a row.
- **A preset per channel:** two channels can play the same theme with different presets.
- **Live file changes:** a file added to a playing theme joins the mix at once; a removed one leaves.
- **New names:** Continuous is now Background, Sparse is Intermittent, Presence is Ebb & Flow, and "How often" is Interval.
- **New look:** a new Theme Editor and Themes page, redesigned Settings pages, one top bar on every page with a **?** help button, and a menu that opens from the top bar on phones.
- **Docker:** remove the Home Assistant or MQTT connection without a restart.
- **Fixes:** a memory leak while playing, group tracks overlapping after a stall, presets changing settings they hadn't saved, and more.
- **Known issue:** Crossfade and Master output gain (Settings → Audio Settings) are saved but don't change the sound yet.

The full list is in the README under What's new. The 1.4.2 development builds below list every change.

### Documentation

- 1.5.0 what's new, help definitions and new screenshots
  ([`c7dfe0b`](https://github.com/synssins/sonorium/commit/c7dfe0bc53a3f0db17129aca2c76142b2d674451))


## v1.4.2-dev.28 (2026-10-10)

### Features

- Metadata.json and presets follow the theme's files
  ([`6d67001`](https://github.com/synssins/sonorium/commit/6d67001b92bf0a776962a5741233aed8fdf4f560))

### Testing

- Keep the add-on package import to the live-track tests
  ([`a1f41b6`](https://github.com/synssins/sonorium/commit/a1f41b69a95b2dd482b3435e1558f2a82a044bef))


## v1.4.2-dev.27 (2026-10-10)

### Features

- Playing channels pick up a theme's new and removed files at once
  ([`6bd8472`](https://github.com/synssins/sonorium/commit/6bd847268a1264ae52519ba3ae4be71c809c400c))


## v1.4.2-dev.26 (2026-10-10)

### Features

- A group picks its next track by weight
  ([`992ca5f`](https://github.com/synssins/sonorium/commit/992ca5fea34f83a1a4e197c95aa05d3561735640))


## v1.4.2-dev.25 (2026-10-10)

### Bug Fixes

- Tracks in a group play start to finish without fading in or out
  ([`4f6ee19`](https://github.com/synssins/sonorium/commit/4f6ee198873f00149eb86c8d050b6c892897c3cb))


## v1.4.2-dev.24 (2026-10-10)

### Bug Fixes

- **ui**: Top-bar buttons one height, square icon buttons, steady right edge
  ([`e0a0e68`](https://github.com/synssins/sonorium/commit/e0a0e682c5467b0ba08bea4b8db31b479ec1fccc))


## v1.4.2-dev.23 (2026-10-10)

### Features

- **ui**: Page actions in the top bar, a help button on every page
  ([`e73b58b`](https://github.com/synssins/sonorium/commit/e73b58bebf3cb9efc185e2f1d6d2b4eac82e4c07))


## v1.4.2-dev.22 (2026-10-10)

### Documentation

- **ui-style**: Phone top bar and menu, full-width cards
  ([`bba8139`](https://github.com/synssins/sonorium/commit/bba81394114ac2c99f8fd0a92c5b8e00082b5b36))

- **ui-style**: Settings column width and connection cards
  ([`04e6f29`](https://github.com/synssins/sonorium/commit/04e6f291567bb81e1bd16a103f2c5b5f604f94e9))

- **ui-style**: Settings pages left-justified, 420px cards, 864px column
  ([`e63fed6`](https://github.com/synssins/sonorium/commit/e63fed669de50ac5e9c6ab2e886d71cbfa7e4f7b))

### Features

- **ui**: Settings pages in the new design; remove HA or MQTT without a restart
  ([`7525c02`](https://github.com/synssins/sonorium/commit/7525c02d4e754b8b6b7f5b55a51cc465fe76ecfa))


## v1.4.2-dev.21 (2026-10-10)

### Documentation

- Explain per-channel presets (before/after); release is 1.5.0
  ([`590e803`](https://github.com/synssins/sonorium/commit/590e803de597232cda3c0a7291e37adcea36c8c8))

- Major release notice, Themes 2.0 what's new and screenshots in the main README
  ([`a6655c6`](https://github.com/synssins/sonorium/commit/a6655c6a51ac072200b873964b5c8aa66072982e))

- READMEs in nine sections with links at the top
  ([`bfc35a1`](https://github.com/synssins/sonorium/commit/bfc35a1081dacc3c4a64ea4707a8e2bcdd9d9f79))

- **addon**: Drop the link row; HA doesn't follow in-page links
  ([`e948ca1`](https://github.com/synssins/sonorium/commit/e948ca14ce9ec3583ace18ece06803afeb36811b))

- **addon**: Major release notice, Themes 2.0 what's new, help definitions, new screenshots
  ([`7a6015a`](https://github.com/synssins/sonorium/commit/7a6015a8a1e490557c84f783286d6fd16d1fac71))

### Features

- **ui**: "How often" is now "Interval"; README explains groups replace Exclusive
  ([`88b2f62`](https://github.com/synssins/sonorium/commit/88b2f623565288f09b5d4b2e71a305d1e8cdabb7))


## v1.4.2-dev.20 (2026-10-10)

### Bug Fixes

- **ui**: Help uses the owner's exact wording for Cancel, Save Preset, Save Theme
  ([`a9f7376`](https://github.com/synssins/sonorium/commit/a9f7376c71b7b9e1af4ce01e983fcba191bdfd96))


## v1.4.2-dev.19 (2026-10-10)

### Bug Fixes

- **ui**: Pages never grow wider than a phone screen
  ([`2075117`](https://github.com/synssins/sonorium/commit/207511778bbc4c397f8fd81fe07f7576831a5116))


## v1.4.2-dev.18 (2026-10-10)

### Bug Fixes

- **ui**: Help wording for Cancel, Save preset and Save theme
  ([`8dd93c2`](https://github.com/synssins/sonorium/commit/8dd93c2b6008a468aec0f5d403ed2f37aa008362))


## v1.4.2-dev.17 (2026-10-10)

### Features

- **ui**: New Themes page: search, category chips, cards or list
  ([`4670c00`](https://github.com/synssins/sonorium/commit/4670c00a2317f31d94f3f7dbe59b352fd384afa1))


## v1.4.2-dev.16 (2026-10-10)

### Features

- Tracks in a group always play Intermittent
  ([`717a235`](https://github.com/synssins/sonorium/commit/717a23579d788de817214832448c9e5232ae19e4))


## v1.4.2-dev.15 (2026-10-10)

### Bug Fixes

- **ui**: Help explains what modes do in a group; drop the word Unique
  ([`1154691`](https://github.com/synssins/sonorium/commit/11546914a6a1a8f9daa87b78e4c2f50f0c0ed994))

- **ui**: Help lists each item as an indented 'Term: text' line
  ([`4b5e5d1`](https://github.com/synssins/sonorium/commit/4b5e5d186b47a385a3aae9a56426ac0a89a5d37d))

### Documentation

- Explain groups instead of a Unique mode
  ([`40dc96e`](https://github.com/synssins/sonorium/commit/40dc96e345c446992f03d737f0a1b50f7b7291d4))


## v1.4.2-dev.14 (2026-10-10)

### Features

- **ui**: Help panel in the Theme Editor explaining modes, groups and saving
  ([`89f76fc`](https://github.com/synssins/sonorium/commit/89f76fcd605e263781d866c272d2188e4b22a2b7))


## v1.4.2-dev.13 (2026-10-10)

### Bug Fixes

- Tracks in a group can never overlap
  ([`b7d5c4a`](https://github.com/synssins/sonorium/commit/b7d5c4a37dab5c14cdaf2e8b314201de0afab210))


## v1.4.2-dev.12 (2026-10-10)

### Features

- **ui**: Clearer playback mode names: Background, Intermittent, Ebb & Flow, Unique
  ([`dbcd2f4`](https://github.com/synssins/sonorium/commit/dbcd2f44c66405b8bb6aa5fc97233c756c8821bf))


## v1.4.2-dev.11 (2026-10-10)

### Bug Fixes

- **ui**: Moving a track into a collapsed group keeps it collapsed
  ([`76e3cc8`](https://github.com/synssins/sonorium/commit/76e3cc85d16ae77aff13665d96003094b3f1388b))


## v1.4.2-dev.10 (2026-10-10)

### Features

- **ui**: Preview mix in the Theme Editor; fix a memory leak in theme streams
  ([`5e50991`](https://github.com/synssins/sonorium/commit/5e50991c578f8ee382d0a44b93f169b7be669fe9))


## v1.4.2-dev.9 (2026-10-10)

### Features

- Hear Theme Editor changes live on playing channels
  ([`01ed309`](https://github.com/synssins/sonorium/commit/01ed309eeee5a184724a706cca33a646a45bb899))


## v1.4.2-dev.8 (2026-10-10)

### Bug Fixes

- **ui**: Save theme closes the editor; Cancel puts back unsaved changes
  ([`745b72b`](https://github.com/synssins/sonorium/commit/745b72b5b3a11c3cd22c3cf97c17f7a9ffab2c43))


## v1.4.2-dev.7 (2026-10-10)

### Bug Fixes

- **ui**: Theme Editor opens with no preset selected
  ([`0640215`](https://github.com/synssins/sonorium/commit/0640215a62a3aa63987be181bf2c44c9fe5e18f9))

### Documentation

- Sonorium UI style, from the Theme Editor redesign
  ([`bc4c588`](https://github.com/synssins/sonorium/commit/bc4c5887c0ccf429f97eddbaab521282e8b11da4))

### Features

- **ui**: Redesigned Theme Edit window
  ([`e989014`](https://github.com/synssins/sonorium/commit/e989014db973b1b619ffdba7f7fc2c57dcae3d8b))


## v1.4.2-dev.6 (2026-10-09)

### Bug Fixes

- **ui**: Wait for the theme rebuild after group changes
  ([`d77f4d8`](https://github.com/synssins/sonorium/commit/d77f4d8ae09a61da19f0c5988b178ef37e6f6a77))

### Features

- Create, rename and delete groups, move tracks between groups
  ([`714208d`](https://github.com/synssins/sonorium/commit/714208d024986b0276517d439c8810818e5afafc))

- **ui**: Groups in the theme editor's track mixer
  ([`2c81f85`](https://github.com/synssins/sonorium/commit/2c81f854ab9eb4a749389c618cee1f225e86dbbe))


## v1.4.2-dev.5 (2026-10-09)

### Bug Fixes

- Groups survive a theme refresh, presets save groups, safe metadata edits
  ([`c72e428`](https://github.com/synssins/sonorium/commit/c72e428e2cc69eaa9e6b30beb173b87ed395033e))


## v1.4.2-dev.4 (2026-10-09)

### Bug Fixes

- Leave headroom for MP3 encoding in the soft limiter
  ([`ad8e643`](https://github.com/synssins/sonorium/commit/ad8e6437c34db404810237e49111a244e3ce298a))


## v1.4.2-dev.3 (2026-10-09)

### Features

- Groups work like a mixer bus
  ([`e84b7a3`](https://github.com/synssins/sonorium/commit/e84b7a3f649f568dbf7e6078bfdbd25fc7207b94))


## v1.4.2-dev.2 (2026-10-09)

### Features

- Group settings with per-track overrides; restore presets from the conversion backup
  ([`1398f19`](https://github.com/synssins/sonorium/commit/1398f193816ca75d448137871a06753cc96f3896))


## v1.4.2-dev.1 (2026-10-09)

### Bug Fixes

- Presets only change the settings they saved; same track order on every system
  ([`8af848a`](https://github.com/synssins/sonorium/commit/8af848a71b33264144a93888578da18505582d59))

- Rebuilt themes use track keys and group folders; theme format spec v2
  ([`affd782`](https://github.com/synssins/sonorium/commit/affd78285ece401e74f7e7b9bfb0a047445a273c))

- Themes with many occasional sounds no longer play quietly
  ([`5fe61c2`](https://github.com/synssins/sonorium/commit/5fe61c2dbdf3fa0268bfc1cea9b307fb74f5711c))

- Two channels on one theme each keep their own preset
  ([`f671a49`](https://github.com/synssins/sonorium/commit/f671a4950c315498bda20942627b9296dfa8b0ef))

### Features

- Theme group folders, one shared theme scanner
  ([`29cb7a1`](https://github.com/synssins/sonorium/commit/29cb7a1a51a86a05b0c051dc647c22d02e3a8111))

- Themes 2.0 — presets.json, conversion and broken-file recovery
  ([`5fb8ae1`](https://github.com/synssins/sonorium/commit/5fb8ae10a8565102e79b7eaafbd1481a8ee05d64))


## v1.4.1 (2026-10-09)


## v1.4.1-dev.5 (2026-10-09)

### Bug Fixes

- Settings menu cut off its last items
  ([`7cb9384`](https://github.com/synssins/sonorium/commit/7cb9384b09302b2f946da09b4a556df860d31b46))


## v1.4.1-dev.4 (2026-10-09)

### Bug Fixes

- Adding floors and areas failed with a 422
  ([`7a499fa`](https://github.com/synssins/sonorium/commit/7a499fa582bac439d16d28add10bb0d21f1260d9))


## v1.4.1-dev.3 (2026-10-09)

### Features

- Floors & Areas, editable outside the HA app
  ([`7eb61af`](https://github.com/synssins/sonorium/commit/7eb61af806490817f6a857a232467576de4ead88))


## v1.4.1-dev.2 (2026-10-09)

### Continuous Integration

- Features raise only the last version number
  ([`56a5e4a`](https://github.com/synssins/sonorium/commit/56a5e4a0677cd124ceb940e001262cb7b5e8bcf5))

### Documentation

- README for 1.4.0 with new screenshots
  ([`22659c7`](https://github.com/synssins/sonorium/commit/22659c716f53407d67013bd2100c8cbefaaa32e6))

### Features

- One install check decides what each install shows
  ([`849091a`](https://github.com/synssins/sonorium/commit/849091a71a43d008e1710e990fd4ba295f5fa3e3))


## v1.5.0-dev.1 (2026-10-09)

### Documentation

- README for 1.4.0 with new screenshots
  ([`22659c7`](https://github.com/synssins/sonorium/commit/22659c716f53407d67013bd2100c8cbefaaa32e6))

### Features

- One install check decides what each install shows
  ([`849091a`](https://github.com/synssins/sonorium/commit/849091a71a43d008e1710e990fd4ba295f5fa3e3))


## v1.4.1-dev.1 (2026-10-09)

### Documentation

- Docker install in the README
  ([`9d407a5`](https://github.com/synssins/sonorium/commit/9d407a5ab4c55205a17d2b348703e5f3d9e81513))


## v1.4.0 (2026-10-09)


## v1.4.0-dev.20 (2026-10-09)

### Bug Fixes

- A channel's preset follows its theme
  ([`dc56043`](https://github.com/synssins/sonorium/commit/dc5604387156dec8cbfd955296bba4f68ddc387a))


## v1.4.0-dev.19 (2026-10-09)

### Bug Fixes

- Don't log zeroconf's port-in-use error that Sonorium handles
  ([`a8eed1b`](https://github.com/synssins/sonorium/commit/a8eed1bff1c14f0447ac3bcf328994d1e661174f))


## v1.4.0-dev.18 (2026-10-09)

### Bug Fixes

- Recognise Denon/Marantz receivers; centre settings pages; dark scrollbars
  ([`7d088fa`](https://github.com/synssins/sonorium/commit/7d088fa7c055b3e63545556b6979be1fbb16952e))

- Refuse to play a channel whose theme doesn't exist
  ([`a3b1d17`](https://github.com/synssins/sonorium/commit/a3b1d176d718dffdd7c93b39f94524de7a356f43))

### Chores

- Remove the old app/docker build
  ([`d67b169`](https://github.com/synssins/sonorium/commit/d67b1694695d7ce49729751d2e8ace905f7d1b58))

### Features

- Logs page in Settings, and a /logs page that works when startup fails
  ([`e7de4bb`](https://github.com/synssins/sonorium/commit/e7de4bb67e5e9d9526b01e28dcd771fdcffe7aac))


## v1.4.0-dev.17 (2026-10-09)

### Continuous Integration

- Push the release commit and tag with retries
  ([`7786e5c`](https://github.com/synssins/sonorium/commit/7786e5c2e7120c5714e85d57ad05c9a5177e6832))

### Features

- See-through logo on speaker screens and in the browser tab
  ([`a209454`](https://github.com/synssins/sonorium/commit/a209454fdbadf06fe2374dd50db70fd185b5c958))


## v1.4.0-dev.16 (2026-10-09)

### Features

- Cast displays show the Sonorium logo while playing
  ([`3167b57`](https://github.com/synssins/sonorium/commit/3167b5771ba9553e4158dd8c1748131fc7cc001a))


## v1.4.0-dev.15 (2026-10-09)

### Bug Fixes

- Speaker settings conversion keeps entries for speakers not currently listed
  ([`793ca7d`](https://github.com/synssins/sonorium/commit/793ca7d74b5958e9163a1fd30296e0fb3d9b3f01))


## v1.4.0-dev.14 (2026-10-09)

### Bug Fixes

- Only speakers switched on in Settings appear; picker hides offline ones
  ([`8984411`](https://github.com/synssins/sonorium/commit/8984411e408a016e7b81b8fec1fda7d45f61154b))


## v1.4.0-dev.13 (2026-10-09)

### Bug Fixes

- Show Sonorium's icon in the browser tab
  ([`aac7bcf`](https://github.com/synssins/sonorium/commit/aac7bcf6b081b20d14fe56475308adf82c2cc01a))


## v1.4.0-dev.12 (2026-10-09)

### Bug Fixes

- Speakers disabled in Settings never appear in or play from channels
  ([`872b24f`](https://github.com/synssins/sonorium/commit/872b24fec3db9a6c1635004d770eec0cd1deaaa5))


## v1.4.0-dev.11 (2026-10-09)

### Bug Fixes

- Settings pages stop stretching on ultrawide screens
  ([`076ebcd`](https://github.com/synssins/sonorium/commit/076ebcd6666a347a460a4043a4ee1194eafce52a))


## v1.4.0-dev.10 (2026-10-09)

### Features

- "Hide offline" switch in Settings > Speakers
  ([`3c792f6`](https://github.com/synssins/sonorium/commit/3c792f63ef989cdab78aedad1329c5d6a9c9e8c4))


## v1.4.0-dev.9 (2026-10-09)

### Features

- **network**: Manual speakers, speaker types and last-scan info in the network service
  ([`10bcc86`](https://github.com/synssins/sonorium/commit/10bcc8622550f76402636a10a062de4e93f98e46))

- **speakers**: Per-speaker settings, merged duplicates, volume offsets and test sound
  ([`7ad4241`](https://github.com/synssins/sonorium/commit/7ad4241e1d1b381c153be14e51df9d5441831b16))

- **ui**: New channel editor, Settings > Speakers and Add speaker dialog
  ([`305735a`](https://github.com/synssins/sonorium/commit/305735a1c1542e244ad190ca05453e34e8b1eb9f))


## v1.4.0-dev.8 (2026-10-09)

### Bug Fixes

- Speaker picker floors and areas tick and untick everything under them
  ([`7245062`](https://github.com/synssins/sonorium/commit/7245062422a60ce524fc50b00cd7c1449f850557))


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
