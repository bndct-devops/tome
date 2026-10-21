# Releasing Tome

How a release is cut, so every one goes the same way. Two kinds: a **minor
release** (features, cut from `main`) and a **patch release** (a hotfix, cut
from the last release tag).

## How the pipeline works

- A push of a `v*` tag runs the tests and the e2e suite, then builds the
  image and publishes `:latest`, `:X`, `:X.Y` and `:X.Y.Z`. A push to `main`
  builds an image but never moves `:latest`.
- Every tag gets a GitHub Release (`gh release create`). Never leave a bare tag.
- The website changelog page renders `CHANGELOG.md`, but `deploy-docs.yml`
  only runs on `website/**` changes. After a release that touches only
  `CHANGELOG.md` / `docs/`, run `gh workflow run deploy-docs.yml --ref main`.
- Release notes live in `docs/release-notes-X.Y.Z.md` and are committed with
  the release.

## Names

- Minor release: a codename from the book-craft theme. Title `vX.Y.0 "Name"`,
  CHANGELOG heading `## [X.Y.0] - YYYY-MM-DD - "Name"`. Do not reuse a name;
  avoid "Grimoire".
- Patch release: no codename. Title `vX.Y.Z`, heading
  `## [X.Y.Z] - YYYY-MM-DD`.

## Minor release

1. Branch `release/vX.Y.0` off `origin/main`.
2. One commit `release: vX.Y.0 "Name"`:
   - move `## [Unreleased]` to `## [X.Y.0] - date - "Name"` and leave an
     empty `## [Unreleased]` above it;
   - `pyproject.toml` version;
   - `docs/release-notes-X.Y.0.md` (format below) and a hero screenshot in
     `docs/screenshots/` if the release has something visual;
   - docs for every new feature; drop `UnreleasedNote` from website pages.
3. PR, wait for CI, squash-merge.
4. Wait until `main` CI is green, including "Build and Push Docker Image".
5. Annotated tag on the merge commit, push it, watch the tag build.
6. `gh release create vX.Y.0 --target main --title 'vX.Y.0 "Name"' --notes-file docs/release-notes-X.Y.0.md --latest`
7. `gh workflow run deploy-docs.yml --ref main`.

## Patch release (hotfix)

Used when a fix must ship before the next minor release and `main` already
holds unreleased work. If `main` holds nothing unreleased, cut the patch from
`main` like a minor release instead.

1. Branch `release/vX.Y.Z` off the last release tag (`git switch -c
   release/vX.Y.Z vX.Y.(Z-1)`), not off `main`.
2. Commit the fix with its tests and a CHANGELOG entry under
   `## [Unreleased]`. Bump the KOReader plugin build if the plugin changed.
3. Commit `release: vX.Y.Z`: `pyproject.toml` version, rename the entries'
   section to `## [X.Y.Z] - date` under an empty `## [Unreleased]`, and
   `docs/release-notes-X.Y.Z.md`.
4. Run the full test suite on the branch.
5. Annotated tag on the release commit: `git tag -a vX.Y.Z -m "Tome vX.Y.Z"
   <release commit>`.
6. Merge `origin/main` into the release branch. The only expected conflict
   is `CHANGELOG.md`: keep `main`'s `[Unreleased]` entries on top, then the
   `[X.Y.Z]` section. The tag stays on the release commit, before this merge.
7. Push the branch and the tag. The tag builds the image and moves `:latest`.
8. Open a PR `release/vX.Y.Z` -> `main` and merge it with **"Create a merge
   commit", not squash**, so the tagged commit stays in `main`'s history and
   nothing has to be ported by hand.
9. `gh release create vX.Y.Z --title vX.Y.Z --notes-file docs/release-notes-X.Y.Z.md --latest`
10. `gh workflow run deploy-docs.yml --ref main`, delete the release branch.
11. Long-lived branches (e.g. `feat/ai-provider`) take `main` with a normal
    merge. If the hotfix bumped the plugin build, the branch moves to the
    next free build number in that merge.

## Release notes

Minor release:

1. `# vX.Y.0 "Name"`, then one or two sentences on the theme or what the
   name means.
2. `## Highlights`: hand-written, the features people will notice, a few
   sentences each, with the hero screenshot.
3. Optional `## Also in this release`, `## Security`, `## KOReader plugin`.
4. `## Upgrade`: the pull command, then migrations, config changes and the
   plugin line (below).
5. `---`, then `## Full changelog` with the plain CHANGELOG section.

Patch release:

1. `# vX.Y.Z`, then one sentence saying what it fixes.
2. `## Fixed`: the entries in prose, ending in `(#NN)`; credit the reporter.
3. `## Upgrade`: the pull command and what (if anything) changes.
4. `---`, then `**Full Changelog**: https://github.com/bndct-devops/tome/compare/vPREV...vX.Y.Z`.

The upgrade command:

```
docker pull ghcr.io/bndct-devops/tome:latest && docker compose up -d
```

KOReader plugin line, when a release ships a new plugin build. The plugin
self-updates, so this is a note, not a warning:

- Old builds keep working: "KOReader: <feature> needs TomeSync build N -
  your plugin self-updates (Settings > Check for updates, or enable
  auto-check on launch)."
- Old builds break: "Update required: TomeSync build N - older builds will
  fail to sync until updated."
