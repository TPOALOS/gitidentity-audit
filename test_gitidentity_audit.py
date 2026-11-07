import os
import shutil
import subprocess
import tempfile
import unittest

import gitidentity_audit as gia

HAVE_GIT = shutil.which("git") is not None
NOREPLY = "123+someone@users.noreply.github.com"
PERSONAL = "jane@gmail.com"


def commit(repo, name, email, msg="m", cname=None, cemail=None):
    env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
               GIT_COMMITTER_NAME=cname or name, GIT_COMMITTER_EMAIL=cemail or email,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(["git", "-C", repo, "commit", "-q", "--allow-empty", "-m", msg],
                   check=True, env=env)


def make_repo(root, name):
    p = os.path.join(root, name)
    os.makedirs(p)
    subprocess.run(["git", "init", "-q", "-b", "main", p], check=True,
                   env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull))
    return p


class ClassifyTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(gia.classify(NOREPLY), "ok")
        self.assertEqual(gia.classify("noreply@example.com"), "ok")
        self.assertEqual(gia.classify(PERSONAL), "personal")
        self.assertEqual(gia.classify("Jane@GMAIL.com"), "personal")
        self.assertEqual(gia.classify("jane@corp.example"), "other")

    def test_allow(self):
        self.assertEqual(gia.classify(PERSONAL, allow=["jane@gmail.com"]), "ok")
        self.assertEqual(gia.classify("a@corp.example", allow_domains=["corp.example"]), "ok")

    def test_parse_log_and_trailer(self):
        raw = gia.SEP_REC + gia.SEP_FIELD.join(
            ["abc", "A", "a@x.org", "C", "c@x.org", "msg\n\nCo-authored-by: Bot <bot@x.org>\n"])
        cm = gia.parse_log(raw)[0]
        self.assertEqual(cm["coauthors"], [("Bot", "bot@x.org")])

    def test_mailmap(self):
        lines = gia.mailmap_lines([("Jane", PERSONAL, "author", 1, "personal")], "Me", NOREPLY)
        self.assertEqual(lines, ["Me <%s> Jane <%s>" % (NOREPLY, PERSONAL)])


@unittest.skipUnless(HAVE_GIT, "git not installed")
class RepoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_repo(self):
        r = make_repo(self.root, "clean")
        commit(r, "me", NOREPLY)
        self.assertEqual(gia.audit_repo(r), [])

    def test_leaky_repo(self):
        r = make_repo(self.root, "leaky")
        commit(r, "Jane", PERSONAL)
        found = gia.audit_repo(r)
        self.assertEqual({(f[1], f[2]) for f in found},
                         {(PERSONAL, "author"), (PERSONAL, "committer")})

    def test_coauthor_trailer_detected(self):
        r = make_repo(self.root, "co")
        commit(r, "me", NOREPLY, msg="x\n\nCo-authored-by: Bob <bob@gmail.com>")
        found = gia.audit_repo(r)
        self.assertEqual([(f[1], f[2]) for f in found], [("bob@gmail.com", "co-author")])

    def test_find_repos(self):
        make_repo(self.root, "a")
        os.makedirs(os.path.join(self.root, "group"))
        make_repo(os.path.join(self.root, "group"), "b")
        names = [os.path.basename(p) for p in gia.find_repos([self.root])]
        self.assertEqual(names, ["a", "b"])

    def test_cli_exit_codes(self):
        r = make_repo(self.root, "x")
        commit(r, "me", NOREPLY)
        self.assertEqual(gia.main([r]), 0)
        commit(r, "Jane", PERSONAL)
        self.assertEqual(gia.main([r]), 1)
        self.assertEqual(gia.main([r, "--allow", PERSONAL]), 0)
        self.assertEqual(gia.main([os.path.join(self.root, "nothing")]), 2)


if __name__ == "__main__":
    unittest.main()
