import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

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


ISOLATED = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def tag(repo, name, email, tag_name, message="release"):
    env = dict(os.environ, GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email, **ISOLATED)
    subprocess.run(["git", "-C", repo, "tag", "-a", tag_name, "-m", message], check=True, env=env)


class NewClassifyTests(unittest.TestCase):
    def test_machine_generated_addresses(self):
        for email in ("jane@Janes-MacBook-Pro.local", "root@localhost", "dev@build01", "me@host.localdomain",
                      "x@nas.lan", "x@pc.internal", "x@laptop.home.arpa"):
            self.assertEqual(gia.classify(email), "machine", email)

    def test_real_domains_are_still_other(self):
        for email in ("jane@corp.example", "jane@mycompany.com", "x@local.example.org"):
            self.assertEqual(gia.classify(email), "other", email)

    def test_machine_addresses_can_be_allowed(self):
        self.assertEqual(gia.classify("ci@runner.local", allow=["ci@runner.local"]), "ok")
        self.assertEqual(gia.classify("ci@runner.local", allow_domains=["runner.local"]), "ok")

    def test_more_noreply_forms(self):
        for email in ("1+me@users.noreply.gitee.com", "noreply@codeberg.org", "me@noreply.example.org"):
            self.assertEqual(gia.classify(email), "ok", email)

    def test_personal_still_personal(self):
        self.assertEqual(gia.classify("a@proton.me"), "personal")


@unittest.skipUnless(HAVE_GIT, "git not installed")
class TaggerAndConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        patcher = mock.patch.dict(os.environ, ISOLATED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_annotated_tag_identity_is_reported(self):
        r = make_repo(self.root, "t")
        commit(r, "me", NOREPLY)
        tag(r, "Jane", PERSONAL, "v1")
        self.assertEqual([(f[1], f[2], f[4]) for f in gia.audit_repo(r)], [(PERSONAL, "tagger", "personal")])

    def test_clean_tagger_and_lightweight_tags_are_fine(self):
        r = make_repo(self.root, "t2")
        commit(r, "me", NOREPLY)
        tag(r, "me", NOREPLY, "v1")
        subprocess.run(["git", "-C", r, "tag", "light"], check=True, env=dict(os.environ))
        self.assertEqual(gia.audit_repo(r), [])
        self.assertEqual(gia.read_taggers(r), [("me", NOREPLY)])

    def test_machine_address_in_history(self):
        r = make_repo(self.root, "m")
        commit(r, "jane", "jane@Janes-MacBook.local")
        self.assertEqual({f[4] for f in gia.audit_repo(r)}, {"machine"})

    def test_configured_email(self):
        r = make_repo(self.root, "c")
        self.assertIsNone(gia.configured_email(r))
        subprocess.run(["git", "-C", r, "config", "user.email", PERSONAL], check=True)
        self.assertEqual(gia.configured_email(r), PERSONAL)

    def test_taggers_of_non_repo_are_empty(self):
        self.assertEqual(gia.read_taggers(self.root), [])


def run_main(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gia.main(list(argv))
    return code, out.getvalue(), err.getvalue()


@unittest.skipUnless(HAVE_GIT, "git not installed")
class NewCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        patcher = mock.patch.dict(os.environ, ISOLATED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_output(self):
        r = make_repo(self.root, "leaky")
        commit(r, "Jane", PERSONAL)
        code, out, _ = run_main(r, "--json", "--replace-name", "me", "--replace-email", NOREPLY)
        data = json.loads(out)[0]
        self.assertEqual(code, 1)
        self.assertFalse(data["ok"])
        self.assertEqual({(f["role"], f["email"], f["commits"]) for f in data["findings"]},
                         {("author", PERSONAL, 1), ("committer", PERSONAL, 1)})
        self.assertEqual(data["mailmap"], ["me <%s> Jane <%s>" % (NOREPLY, PERSONAL)])

    def test_json_clean_repo(self):
        r = make_repo(self.root, "clean")
        commit(r, "me", NOREPLY)
        code, out, _ = run_main(r, "--json")
        data = json.loads(out)[0]
        self.assertEqual((code, data["ok"], data["findings"], data["config"]), (0, True, [], None))

    def test_config_warning_does_not_fail_by_default(self):
        r = make_repo(self.root, "cfg")
        commit(r, "me", NOREPLY)
        subprocess.run(["git", "-C", r, "config", "user.email", PERSONAL], check=True)
        code, out, err = run_main(r)
        self.assertEqual(code, 0)
        self.assertIn("new commits in this repo will use %s (personal)" % PERSONAL, out)
        self.assertIn("configured to leak", err)
        self.assertEqual(run_main(r, "--fail-on-config")[0], 1)

    def test_safe_config_is_silent(self):
        r = make_repo(self.root, "cfg2")
        commit(r, "me", NOREPLY)
        subprocess.run(["git", "-C", r, "config", "user.email", NOREPLY], check=True)
        code, out, _ = run_main(r, "--fail-on-config", "--json")
        data = json.loads(out)[0]
        self.assertEqual((code, data["config"]), (0, {"email": NOREPLY, "kind": "ok"}))

    def test_allow_applies_to_config(self):
        r = make_repo(self.root, "cfg3")
        commit(r, "me", NOREPLY)
        subprocess.run(["git", "-C", r, "config", "user.email", PERSONAL], check=True)
        self.assertEqual(run_main(r, "--fail-on-config", "--allow", PERSONAL)[0], 0)

    def test_text_output_still_marks_leaks(self):
        r = make_repo(self.root, "txt")
        commit(r, "Jane", PERSONAL)
        _, out, _ = run_main(r)
        self.assertTrue(out.startswith("LEAK "))
        self.assertIn("personal  author", out)

    def test_values_never_include_commit_messages(self):
        r = make_repo(self.root, "msg")
        commit(r, "Jane", PERSONAL, msg="secret commit message")
        self.assertNotIn("secret commit message", run_main(r, "--json")[1])


if __name__ == "__main__":
    unittest.main()
