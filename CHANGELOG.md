# Changelog

## 1.1.0

- Annotated-tag authors (`tagger`) are now checked
- New `machine` kind for addresses git built from the computer name (`user@host.local`, `root@localhost`)
- `config` warning when a repository's `user.email` would leak in new commits (`--fail-on-config` to fail on it)
- `--json` output; more noreply forms (Gitee, Codeberg)
- Installable with pip (`gitidentity-audit` command), CI on Linux and macOS

## 1.0.0

Initial release.
