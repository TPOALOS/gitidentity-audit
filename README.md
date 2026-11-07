# gitidentity-audit

Find personal e-mail addresses leaked in your git history. It scans every git
repository under the paths you give it and lists the author, committer and
`Co-authored-by` identities found in **all** commits and branches. Anything that is
not a `noreply` address is reported.

Read-only: it never rewrites history. Standard library only (requires the `git` command).

## Why

Git stores the e-mail from `git config user.email` in every commit, and public
repositories expose it to anyone. Many people only notice after publishing.

## Usage

```bash
python3 gitidentity_audit.py ~/projects
python3 gitidentity_audit.py ./my-repo --allow-domain mycompany.com
python3 gitidentity_audit.py ~/projects --replace-name "me" \
    --replace-email "12345+me@users.noreply.github.com"
python3 gitidentity_audit.py ~/projects --json --fail-on-config
# or: pip install git+https://github.com/TPOALOS/gitidentity-audit && gitidentity-audit ~/projects
```

Example output:

```
OK   /home/me/projects/clean-repo
LEAK /home/me/projects/old-repo
     personal  author    Jane <jane@gmail.com>  (14 commit(s))
     personal  committer Jane <jane@gmail.com>  (14 commit(s))
     personal  tagger    Jane <jane@gmail.com>  (2 commit(s))
     machine   author    jane <jane@Janes-MacBook.local>  (3 commit(s))
     config    new commits in this repo will use jane@gmail.com (personal)
```

- `personal` = common consumer mail provider, `machine` = an address git built from the computer's name because
  `user.email` was never set (`jane@Janes-MacBook.local`, `root@localhost`: it leaks the machine and account name),
  `other` = any other non-noreply address.
- Roles are `author`, `committer`, `co-author` (trailers) and `tagger` (annotated tags).
- The `config` line shows the `user.email` git would use for the **next** commit in that repository. It is a warning and does not
  change the exit code unless you pass `--fail-on-config`, which is handy in a pre-push hook or CI.
- `--allow EMAIL` / `--allow-domain DOMAIN` mark addresses you accept (repeatable).
- With `--replace-name/--replace-email` it prints `.mailmap` lines. A `.mailmap` only
  changes how tools display names locally; to remove an address from published history
  you must rewrite it (for example with `git filter-repo`) and force-push. Old commits
  can stay reachable on the host until the repository is recreated or the host purges them.

Prevent future leaks: `git config --global user.email "ID+USERNAME@users.noreply.github.com"`.

Exit codes: `0` clean, `1` findings, `2` no repositories found.

## Tests

```bash
python3 -m unittest -v
```

## License

MIT (c) TPOALOS
