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
import os
import re
import subprocess
import sys

__version__ = "1.0.0"

SEP_FIELD, SEP_REC = "\x1f", "\x1e"
FORMAT = SEP_REC + SEP_FIELD.join(["%H", "%an", "%ae", "%cn", "%ce", "%B"])
TRAILER = re.compile(r"(?im)^co-authored-by:\s*(.*?)\s*<([^>]+)>\s*$")
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com", "outlook.com",
    "hotmail.com", "live.com", "yahoo.com", "proton.me", "protonmail.com", "pm.me",
    "hey.com", "aol.com", "gmx.com", "gmx.de", "yandex.com", "mail.ru", "qq.com",
}
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__"}


def is_noreply(email):
    e = email.lower()
    return e.endswith("@users.noreply.github.com") or e.startswith("noreply@") \
        or "@noreply." in e or e.endswith("@users.noreply.gitlab.com")


def classify(email, allow=(), allow_domains=()):
    """Return 'ok', 'personal' or 'other' for an e-mail address."""
    e = email.lower()
    if is_noreply(e) or e in {a.lower() for a in allow}:
        return "ok"
    domain = e.rsplit("@", 1)[-1]
    if domain in {d.lower() for d in allow_domains}:
        return "ok"
    return "personal" if domain in PERSONAL_DOMAINS else "other"


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


def identities(commits):
    """Counter of (name, email, role) across commits."""
    c = collections.Counter()
    for cm in commits:
        c[cm["author"] + ("author",)] += 1
        c[cm["committer"] + ("committer",)] += 1
        for n, e in cm["coauthors"]:
            c[(n, e, "co-author")] += 1
    return c


def audit_repo(path, allow=(), allow_domains=()):
    """Return list of findings (name, email, role, count, kind) for one repo."""
    findings = []
    for (name, email, role), n in sorted(identities(read_repo(path)).items()):
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
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    repos = find_repos(args.paths)
    if not repos:
        print("gitidentity-audit: no git repositories found", file=sys.stderr)
        return 2
    total = 0
    for repo in repos:
        try:
            findings = audit_repo(repo, args.allow, args.allow_domain)
        except (RuntimeError, OSError) as exc:
            print("%s: error: %s" % (repo, exc), file=sys.stderr)
            continue
        if not findings:
            print("OK   %s" % repo)
            continue
        total += len(findings)
        print("LEAK %s" % repo)
        for name, email, role, n, kind in findings:
            print("     %-9s %-8s %s <%s>  (%d commit(s))" % (kind, role, name, email, n))
        if args.replace_name and args.replace_email:
            print("     suggested .mailmap (display only; does not change history):")
            for line in mailmap_lines(findings, args.replace_name, args.replace_email):
                print("       " + line)
    print("gitidentity-audit: %d non-noreply identit%s in %d repo(s)" % (
        total, "y" if total == 1 else "ies", len(repos)), file=sys.stderr)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
