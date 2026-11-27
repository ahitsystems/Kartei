# Changelog entries

Every pull request with a change users or administrators notice adds **one file here** instead of editing `CHANGELOG.md`, so parallel pull requests don't collide.

- Name: `<short-name>.<type>.md`, e.g. `phone-view.added.md`. Type is one of `added`, `changed`, `deprecated`, `removed`, `fixed`, `security`.
- Content: one or more bullet lines in the style of `CHANGELOG.md`, e.g. `- **Phone view:** …`. No heading; the file name gives the section.
- Several kinds of change in one pull request: one file per type.

CI checks the files (`python3 scripts/changelog.py check`); `python3 scripts/changelog.py preview` shows the next release's section. At a release, `python3 scripts/changelog.py release <version>` moves them into `CHANGELOG.md` and deletes them (see CONTRIBUTING.md).
