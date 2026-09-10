"""What the client-name guard must see, and what it must leave alone.

Written adversarially in both directions: a guard that only proves it catches
things is half a guard -- the false positives are what get it disabled.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_client_names.py")


def run(lines, patterns=None, name="sample.py"):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("\n".join(lines) + "\n", encoding="utf8")
        env = dict(os.environ)
        env.pop("CLIENT_NAME_PATTERNS", None)
        if patterns is not None:
            env["CLIENT_NAME_PATTERNS"] = patterns
        r = subprocess.run(
            [sys.executable, str(SCRIPT), str(p)], capture_output=True, text=True, env=env, cwd=tmp
        )
        return r.returncode, r.stdout


class TestItCatches(unittest.TestCase):
    def test_an_instance_id(self):
        code, out = run(["# measured on SRV-0127, 63 orphan pages"])
        self.assertEqual(code, 1)
        self.assertIn("an instance id", out)

    def test_a_client_subdomain(self):
        code, out = run(["// seen on acmecorp.neoffice.me yesterday"])
        self.assertEqual(code, 1)
        self.assertIn("a client subdomain", out)

    def test_a_company_name_when_the_list_is_supplied(self):
        code, out = run(["# reproduced at Acme & Sons SA"], patterns=r"\bacme\b")
        self.assertEqual(code, 1)
        self.assertIn("a client name", out)


class TestItLeavesAlone(unittest.TestCase):
    def test_our_own_instances(self):
        for host in ("osiris", "neoservice", "demo"):
            code, _ = run([f"# reproduced on {host}.neoffice.me"])
            self.assertEqual(code, 0, host)

    def test_a_company_name_with_no_list(self):
        """Without the secret the name half must not run -- and must say so."""
        code, out = run(["# reproduced at Acme & Sons SA"])
        self.assertEqual(code, 0)
        self.assertIn("CLIENT_NAME_PATTERNS is not set", out)

    def test_a_build_artefact(self):
        code, _ = run(["var x = 'SRV-0127'"], name="public/frontend/assets/app-a1b2.js")
        self.assertEqual(code, 0)

    def test_a_minified_bundle(self):
        code, _ = run(["var x='SRV-0127'"], name="bundle.min.js")
        self.assertEqual(code, 0)

    def test_a_binary_like_extension(self):
        code, _ = run(["SRV-0127"], name="logo.svg")
        self.assertEqual(code, 0)

    def test_a_word_that_merely_contains_srv(self):
        code, _ = run(["# the SRV record and SRV-01 are not instance ids"])
        self.assertEqual(code, 0)


class TestTheReport(unittest.TestCase):
    def test_it_never_echoes_what_it_found(self):
        """A CI log is as public as the file it guards."""
        code, out = run(["# measured on SRV-0127 during the incident"])
        self.assertEqual(code, 1)
        self.assertNotIn("SRV-0127", out)

    def test_it_gives_a_path_and_a_line(self):
        code, out = run(["ok", "ok", "# on SRV-0127"])
        self.assertEqual(code, 1)
        self.assertIn("sample.py:3", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
