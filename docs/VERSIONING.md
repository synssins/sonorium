# Add-on versioning

Version numbers for the Home Assistant add-on are set automatically. Don't edit
`sonorium_addon/sonorium/version`, `config.yaml` or the Dockerfile label by hand.

## How it works

Home Assistant offers an add-on update whenever the `version` in
`sonorium_addon/config.yaml` is different from the installed one. The
[Add-on Version](../.github/workflows/addon-version.yml) workflow makes sure every
push that touches `sonorium_addon/` gets a new one:

1. [python-semantic-release](https://python-semantic-release.readthedocs.io/)
   reads the commit messages since the last `addon-v*` tag
   ([Conventional Commits](https://www.conventionalcommits.org/)).
2. It writes the new version to `sonorium_addon/sonorium/version`, and
   `sync_version.py` copies it into `config.yaml` and the Dockerfile.
3. It commits that as `github-actions[bot]`, tags it `addon-vX.Y.Z`, and updates
   `sonorium_addon/CHANGELOG.md` (shown in HA's update dialog).

## Release order

Home Assistant doesn't build the add-on on the device: `config.yaml` has
`image: ghcr.io/synssins/{arch}-addon-sonorium`, so HA downloads the ready-made
image for the version in `config.yaml`. That image has to exist before HA hears
about the version, so the workflow runs in this order:

1. **Make release commit**: the version commit is made, and only its
   `addon-v*` tag is pushed. The tag reserves the number (all branches share
   the `-dev.N` count); HA reads branches, not tags, so nobody sees it yet.
2. **Add-on images**: the add-on is built from that tag for each architecture
   (`amd64`, `aarch64`) and pushed as `:<version>`.
3. **Push release commit**: runs only if every image was pushed. The branch
   moves to the version commit, and HA can now offer the update. If a build
   failed, the branch keeps its old version, nothing is offered, and the tag
   is deleted again.
4. **Tags and the standalone image**: `:latest` (main), `:dev` (`fixes`) or
   `:branch-<name>` (any other branch) is moved to the new version, and the
   standalone image `ghcr.io/synssins/sonorium` is built as before.

If someone pushes to the branch while the images are building, step 3 stops
without pushing; that push has its own run, which makes the next version.

| Branch | Versions | Use |
|---|---|---|
| `main` | `1.2.89`, `1.3.0`, `2.0.0` | Releases |
| anything else | `1.2.89-dev.1`, `1.2.89-dev.2`, … | Testing a branch in HA |

Config: [`releaserc.toml`](../releaserc.toml). The plain `v*` tags belong to the
Windows app release workflow and are unrelated.

## Commit messages

| Prefix | Bump | Example |
|---|---|---|
| `feat:` `fix:` `perf:` `refactor:` `build:` `revert:` | patch | `feat: fade in/out between themes` |
| `feat!:` or a `BREAKING CHANGE:` footer | major | `feat!: new state file format` |
| anything else (`docs:`, `chore:`, no prefix) | forced by the workflow so HA still sees the change: next `-dev.N` on a branch, patch on `main` | |

A minor or major release is never automatic. To make one, open **Actions → Add-on
Version → Run workflow**, pick the branch, and set **bump** to `minor` or `major`.

## Working on a branch

The bot pushes a release commit after each of your pushes, so run `git pull`
before pushing again. Force-pushing is blocked by the repo ruleset.

To test a branch in Home Assistant, add
`https://github.com/synssins/sonorium#<branch>` as an add-on repository. It
installs as a separate add-on, so stop the `main` one first (both use port 8008).
