#!/usr/bin/env python3
"""gitidentity-audit - find personal e-mail addresses leaked in git history.

Scans every git repository under the given paths and lists the author and
committer identities (and Co-authored-by trailers) found in all commits. Any
address that is not a "noreply" address is reported, and a .mailmap
suggestion is printed. Read-only; it never rewrites history.
Standard library only (needs the git command). Exit code 0 = clean, 1 = findings, 2 = error.
"""
import argparse
import collections
import json
import os
import re
import subprocess
import sys

__version__ = "1.1.0"

SEP_FIELD, SEP_REC = "\x1f", "\x1e"
FORMAT = SEP_REC + SEP_FIELD.join(["%H", "%an", "%ae", "%cn", "%ce", "%B"])
TRAILER = re.compile(r"(?im)^co-authored-by:\s*(.*?)\s*<([^>]+)>\s*$")
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com", "outlook.com",
    "hotmail.com", "live.com", "yahoo.com", "proton.me", "protonmail.com", "pm.me",
    "hey.com", "aol.com", "gmx.com", "gmx.de", "yandex.com", "mail.ru", "qq.com",
}
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__"}
# addresses built from the computer's name when user.email was never configured (they leak the machine and user name)
MACHINE_SUFFIXES = (".local", ".localdomain", ".localhost", ".lan", ".internal", ".home", ".home.arpa", ".intranet",
                    ".corp", ".private")


def is_noreply(email):
    e = email.lower()
    return e.endswith("@users.noreply.github.com") or e.startswith("noreply@") \
        or "@noreply." in e or e.endswith("@users.noreply.gitlab.com") or e.endswith("@users.noreply.gitee.com")


def classify(email, allow=(), allow_domains=()):
    """Return 'ok', 'personal', 'machine' (host-derived address) or 'other' for an e-mail address."""
    e = email.lower()
    if is_noreply(e) or e in {a.lower() for a in allow}:
        return "ok"
    domain = e.rsplit("@", 1)[-1]
    if domain in {d.lower() for d in allow_domains}:
        return "ok"
    if domain in PERSONAL_DOMAINS:
        return "personal"
    if "." not in domain or domain.endswith(MACHINE_SUFFIXES):
        return "machine"
    return "other"


def find_repos(paths, max_depth=4):
    repos = []
    for root in paths:
        root = os.path.abspath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)
        for dirpath, dirs, files in os.walk(root):
            if ".git" in dirs or ".git" in files:
                repos.append(dirpath)
                dirs[:] = []
                continue
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
                       and dirpath.count(os.sep) - base_depth < max_depth]
    return sorted(set(repos))


def parse_log(output):
    """Parse `git log` output into a list of commit dicts."""
    commits = []
    for rec in output.split(SEP_REC):
        if not rec.strip():
            continue
        parts = rec.split(SEP_FIELD, 5)
        if len(parts) < 6:
            continue
        sha, an, ae, cn, ce, body = parts
        commits.append({"sha": sha.strip(), "author": (an, ae), "committer": (cn, ce),
                        "coauthors": [(n, e) for n, e in TRAILER.findall(body)]})
    return commits


def read_repo(path):
    out = subprocess.run(["git", "-C", path, "log", "--all", "--format=" + FORMAT],
                         capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "git log failed")
    return parse_log(out.stdout)


def read_taggers(path):
    """(name, email) of every annotated tag; lightweight tags carry no identity."""
    out = subprocess.run(["git", "-C", path, "for-each-ref", "--format=%(taggername)" + SEP_FIELD + "%(taggeremail)",
                          "refs/tags"], capture_output=True, text=True, errors="replace")
    if out.returncode != 0:
        return []
    taggers = []
    for line in out.stdout.splitlines():
        name, _, email = line.partition(SEP_FIELD)
        email = email.strip().strip("<>")
        if email:
            taggers.append((name, email))
    return taggers


def configured_email(path):
    """The user.email git would use for a new commit in ``path`` (None when unset)."""
    out = subprocess.run(["git", "-C", path, "config", "user.email"], capture_output=True, text=True, errors="replace")
    return out.stdout.strip() or None if out.returncode == 0 else None


def identities(commits, taggers=()):
    """Counter of (name, email, role) across commits."""
    c = collections.Counter()
    for cm in commits:
        c[cm["author"] + ("author",)] += 1
        c[cm["committer"] + ("committer",)] += 1
        for n, e in cm["coauthors"]:
            c[(n, e, "co-author")] += 1
    for n, e in taggers:
        c[(n, e, "tagger")] += 1
    return c


def audit_repo(path, allow=(), allow_domains=()):
    """Return list of findings (name, email, role, count, kind) for one repo."""
    findings = []
    for (name, email, role), n in sorted(identities(read_repo(path), read_taggers(path)).items()):
        kind = classify(email, allow, allow_domains)
        if kind != "ok":
            findings.append((name, email, role, n, kind))
    return findings


def mailmap_lines(findings, new_name, new_email):
    seen = sorted({(n, e) for n, e, _, _, _ in findings})
    return ["%s <%s> %s <%s>" % (new_name, new_email, n, e) for n, e in seen]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find leaked personal e-mails in git history.")
    ap.add_argument("paths", nargs="*", default=["."], help="repo or folder containing repos")
    ap.add_argument("--allow", action="append", default=[], help="e-mail you accept (repeatable)")
    ap.add_argument("--allow-domain", action="append", default=[],
                    help="domain you accept, e.g. mycompany.com (repeatable)")
    ap.add_argument("--replace-name", help="name for .mailmap suggestion")
    ap.add_argument("--replace-email", help="e-mail for .mailmap suggestion")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--fail-on-config", action="store_true",
                    help="also exit 1 when a repository's configured user.email would leak in new commits")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    repos = find_repos(args.paths)
    if not repos:
        print("gitidentity-audit: no git repositories found", file=sys.stderr)
        return 2
    total, config_bad, report = 0, 0, []
    for repo in repos:
        try:
            findings = audit_repo(repo, args.allow, args.allow_domain)
        except (RuntimeError, OSError) as exc:
            print("%s: error: %s" % (repo, exc), file=sys.stderr)
            continue
        cfg = configured_email(repo)
        cfg_kind = classify(cfg, args.allow, args.allow_domain) if cfg else None
        cfg_leaks = cfg_kind not in (None, "ok")
        config_bad += cfg_leaks
        total += len(findings)
        mailmap = mailmap_lines(findings, args.replace_name, args.replace_email) \
            if findings and args.replace_name and args.replace_email else []
        report.append({"repo": repo, "ok": not findings,
                       "findings": [{"kind": k, "role": r, "name": n, "email": e, "commits": c}
                                    for n, e, r, c, k in findings],
                       "config": {"email": cfg, "kind": cfg_kind} if cfg else None, "mailmap": mailmap})
        if args.json:
            continue
        print("%s %s" % ("LEAK" if findings else "OK  ", repo))
        for name, email, role, n, kind in findings:
            print("     %-9s %-9s %s <%s>  (%d commit(s))" % (kind, role, name, email, n))
        if cfg_leaks:
            print("     config    new commits in this repo will use %s (%s)" % (cfg, cfg_kind))
        if mailmap:
            print("     suggested .mailmap (display only; does not change history):")
            for line in mailmap:
                print("       " + line)
    if args.json:
        print(json.dumps(report, indent=2))
    print("gitidentity-audit: %d non-noreply identit%s in %d repo(s)%s" % (
        total, "y" if total == 1 else "ies", len(repos),
        "; %d repo(s) configured to leak in new commits" % config_bad if config_bad else ""), file=sys.stderr)
    return 1 if total or (args.fail_on_config and config_bad) else 0

if __name__ == "__main__":
    sys.exit(main())
