"""scripts/ci_apt_install.sh: the install gets through an outage of the runner's own Ubuntu archives (neoffice-maintenance#2542).

On 2026-11-11 the runners stopped resolving azure.archive.ubuntu.com, archive.ubuntu.com and security.ubuntu.com while
everything else answered, and every app CI died in its install step. The helper is run here against a copy of a
runner's apt configuration, with `sudo`, `timeout`, `getent`, `curl` and `apt-get` replaced by fakes that read which
hosts are "down" from the environment.

Run: python3 -m unittest scripts/test_ci_apt_install.py
"""

import os
import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# CI_APT_HELPER lets the same scenarios run against another helper (the old step's commands, to see them fail)
HELPER = Path(os.environ.get("CI_APT_HELPER", HERE / "ci_apt_install.sh"))
WORKFLOW = HERE.parent / ".github" / "workflows" / "frappe-app-ci.yml"
RUNNER_HOSTS = ["azure.archive.ubuntu.com", "archive.ubuntu.com", "security.ubuntu.com"]
MIRRORS = "http://mirror-one.example.org/ubuntu http://mirror-two.example.org/ubuntu"

FAKE_APT = r'''#!/usr/bin/env python3
"""A fake apt-get: `update` fails when a host of the active sources is down, `install` when a package is unknown."""
import os, re, sys
from pathlib import Path

apt_dir = Path(os.environ["CI_APT_DIR"])
down = os.environ.get("FAKE_DOWN", "").split()
missing = os.environ.get("FAKE_MISSING", "").split()
log = Path(os.environ["FAKE_LOG"])
args = sys.argv[1:]

def active_lines():
    files = [apt_dir / "sources.list", *sorted((apt_dir / "sources.list.d").glob("*.list")),
             *sorted((apt_dir / "sources.list.d").glob("*.sources"))]
    for f in files:
        if f.is_file():
            for line in f.read_text().splitlines():
                if not line.lstrip().startswith("#"):
                    yield line

def hosts():
    lines = list(active_lines())
    for ref in re.findall(r"mirror\+file:(\S+)", "\n".join(lines)):
        if Path(ref).is_file():
            lines += Path(ref).read_text().splitlines()
    return sorted(set(re.findall(r"https?://([^/\s:]+)", "\n".join(lines))))

with log.open("a") as out:
    out.write("apt-get " + " ".join(args) + "\n")
    if "update" in args:
        counter = Path(os.environ["FAKE_UPDATE_FAILS_FILE"])
        left = int(counter.read_text()) if counter.exists() else 0
        if left > 0:
            counter.write_text(str(left - 1))
            print("E: Failed to fetch (a blip)")
            sys.exit(100)
        dead = [h for h in hosts() if h in down]
        if dead:
            for h in dead:
                print(f"Err: http://{h}/ubuntu InRelease\n  Temporary failure resolving '{h}'")
            sys.exit(100)
        out.write("updated from " + " ".join(hosts()) + "\n")
    elif "install" in args:
        packages = [a for a in args[args.index("install") + 1:] if not a.startswith("-")]
        dead = [h for h in hosts() if h in down]
        if dead:
            print(f"Err: http://{dead[0]}/ubuntu ... Temporary failure resolving '{dead[0]}'")
            print("E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?")
            sys.exit(100)
        unknown = [p for p in packages if p in missing]
        if unknown:
            print(f"E: Unable to locate package {unknown[0]}")
            sys.exit(100)
        out.write("installed " + " ".join(packages) + " from " + " ".join(hosts()) + "\n")
'''

FAKES = {
    "sudo": '#!/bin/sh\nexec "$@"\n',
    "timeout": '#!/bin/sh\nshift\nexec "$@"\n',
    "sleep": "#!/bin/sh\nexit 0\n",
    # getent hosts <name>
    "getent": textwrap.dedent(
        """\
        #!/bin/sh
        echo "getent $2" >> "$FAKE_LOG"
        case " $FAKE_DOWN " in *" $2 "*) exit 2;; esac
        echo "192.0.2.1 $2"
        """
    ),
    # curl ... <url>: down host -> no answer; FAKE_NO_RELEASE -> 404
    "curl": textwrap.dedent(
        """\
        #!/bin/sh
        for url; do :; done
        host=$(echo "$url" | sed -E 's#^https?://##; s#[:/].*##')
        echo "curl $url" >> "$FAKE_LOG"
        case " $FAKE_DOWN " in *" $host "*) exit 6;; esac
        case " $FAKE_NO_RELEASE " in *" $host "*) exit 22;; esac
        exit 0
        """
    ),
    "apt-get": FAKE_APT,
}

# /etc/apt/sources.list.d/ubuntu.sources and /etc/apt/apt-mirrors.txt of an ubuntu-26.04 runner (image 20261101.186.1),
# as the probe of 2026-11-11 printed them (comments shortened)
UBUNTU_SOURCES = """\
## Note, this file is written by cloud-init on first boot of an instance
Types: deb
URIs: mirror+file:{apt}/apt-mirrors.txt
Suites: resolute resolute-updates resolute-backports
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

## Ubuntu security updates. Aside from URIs and Suites,
## this should mirror your choices in the previous section.
Types: deb
URIs: mirror+file:{apt}/apt-mirrors.txt
Suites: resolute-security
Components: main universe restricted multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
"""

APT_MIRRORS = (
    "http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n"
    "https://archive.ubuntu.com/ubuntu/\tpriority:2\n"
    "https://security.ubuntu.com/ubuntu/\tpriority:3\n"
)


class Runner:
    """A runner's apt configuration in a temporary directory, and the fakes that answer for its network."""

    def __init__(self, test):
        self.root = Path(tempfile.mkdtemp(prefix="ci-apt-"))
        test.addCleanup(shutil.rmtree, self.root, True)
        self.apt = self.root / "etc-apt"
        (self.apt / "sources.list.d").mkdir(parents=True)
        (self.apt / "sources.list").write_text("# Ubuntu sources have moved to sources.list.d/ubuntu.sources\n")
        (self.apt / "sources.list.d" / "ubuntu.sources").write_text(UBUNTU_SOURCES.format(apt=self.apt))
        (self.apt / "apt-mirrors.txt").write_text(APT_MIRRORS)
        self.os_release = self.root / "os-release"
        self.os_release.write_text('NAME="Ubuntu"\nVERSION_ID="26.04"\nVERSION_CODENAME=resolute\n')
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in FAKES.items():
            target = self.bin / name
            target.write_text(body)
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
        # the helper sources ci_retry.sh from its own directory, as it does in ~ on the runner
        self.helper_dir = self.root / "home"
        self.helper_dir.mkdir()
        shutil.copy(HELPER, self.helper_dir / "ci_apt_install.sh")
        shutil.copy(HERE / "ci_retry.sh", self.helper_dir / "ci_retry.sh")
        self.log = self.root / "log"
        self.log.touch()
        self.update_fails = self.root / "update-fails"

    def run(self, *packages, down=(), missing=(), no_release=(), update_fails=0):
        self.update_fails.write_text(str(update_fails))
        env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            CI_APT_DIR=str(self.apt),
            CI_OS_RELEASE=str(self.os_release),
            CI_APT_MIRRORS=MIRRORS,
            CI_APT_RESOLVE_PAUSE="0",
            CI_RETRY_DELAY="0",
            FAKE_DOWN=" ".join(down),
            FAKE_MISSING=" ".join(missing),
            FAKE_NO_RELEASE=" ".join(no_release),
            FAKE_LOG=str(self.log),
            FAKE_UPDATE_FAILS_FILE=str(self.update_fails),
        )
        env.pop("CI_RETRY_ATTEMPTS", None)
        return subprocess.run(
            ["bash", str(self.helper_dir / "ci_apt_install.sh"), *packages],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )

    def lines(self, prefix):
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def active_sources(self):
        return {p.name: p.read_text() for p in sorted((self.apt / "sources.list.d").glob("*.sources"))}


PACKAGES = ("mariadb-client", "xfonts-75dpi", "xfonts-base")


class TestTheRunnersOwnArchives(unittest.TestCase):
    def test_when_they_answer_apt_runs_once_from_them_and_nothing_is_rewritten(self):
        runner = Runner(self)
        out = runner.run(*PACKAGES)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(runner.lines("updated")), 1)
        self.assertEqual(
            runner.lines("installed"),
            ["installed mariadb-client xfonts-75dpi xfonts-base from " + " ".join(sorted(RUNNER_HOSTS))],
        )
        self.assertEqual(list(runner.active_sources()), ["ubuntu.sources"])
        self.assertNotIn("::warning::", out.stdout)

    def test_every_apt_call_retries_its_downloads_and_the_update_fails_on_any_index_it_missed(self):
        runner = Runner(self)
        runner.run(*PACKAGES)
        calls = runner.lines("apt-get")
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("-o Acquire::Retries=5", call)
        update = [c for c in calls if " update" in c]
        self.assertTrue(update and all("--error-on=any" in c for c in update), calls)

    def test_an_update_that_fails_for_a_moment_is_tried_again_from_the_same_archives(self):
        runner = Runner(self)
        out = runner.run(*PACKAGES, update_fails=2)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len([c for c in runner.lines("apt-get") if " update" in c]), 3)
        self.assertEqual(list(runner.active_sources()), ["ubuntu.sources"])


class TestTheOutageOfTheRunnersArchives(unittest.TestCase):
    """The outage of 2026-11-11: the runner resolves none of the Ubuntu hosts its sources name."""

    def test_the_packages_are_installed_from_a_mirror_that_answers(self):
        runner = Runner(self)
        out = runner.run(*PACKAGES, down=RUNNER_HOSTS)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(
            runner.lines("installed"), ["installed mariadb-client xfonts-75dpi xfonts-base from mirror-one.example.org"]
        )
        self.assertIn("::warning::the runner does not resolve", out.stdout)
        self.assertIn("::warning::apt uses http://mirror-one.example.org/ubuntu", out.stdout)

    def test_the_mirror_is_held_to_the_keyring_components_and_release_of_the_runners_sources(self):
        runner = Runner(self)
        runner.run(*PACKAGES, down=RUNNER_HOSTS)
        sources = runner.active_sources()
        self.assertEqual(list(sources), ["ubuntu-mirror.sources"])
        text = sources["ubuntu-mirror.sources"]
        self.assertIn("URIs: http://mirror-one.example.org/ubuntu\n", text)
        self.assertIn("Suites: resolute resolute-updates resolute-backports resolute-security\n", text)
        self.assertIn("Components: main universe restricted multiverse\n", text)
        self.assertIn("Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n", text)
        # the runner's own sources are set aside, not destroyed
        self.assertTrue((runner.apt / "sources-of-the-runner" / "ubuntu.sources").is_file())

    def test_the_dead_archives_are_not_retried_for_minutes_before_the_mirror(self):
        runner = Runner(self)
        runner.run(*PACKAGES, down=RUNNER_HOSTS)
        # one dead name is enough: no apt call ever goes to the runner's archives
        self.assertFalse([line for line in runner.lines("updated") if "ubuntu.com" in line])
        self.assertLessEqual(len(runner.lines("getent")), 6)

    def test_a_host_a_source_names_directly_counts_as_much_as_one_of_the_mirror_list(self):
        runner = Runner(self)
        sources = runner.apt / "sources.list.d" / "ubuntu.sources"
        sources.write_text(
            sources.read_text().replace(f"mirror+file:{runner.apt}/apt-mirrors.txt\nSuites: resolute-security",
                                        "http://security.ubuntu.com/ubuntu\nSuites: resolute-security")
        )
        (runner.apt / "apt-mirrors.txt").write_text("http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n")
        out = runner.run(*PACKAGES, down=["security.ubuntu.com"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("does not resolve security.ubuntu.com", out.stdout)
        self.assertEqual(runner.lines("installed")[-1].split(" from ")[1], "mirror-one.example.org")

    def test_a_mirror_that_is_down_or_lacks_the_release_is_passed_over(self):
        runner = Runner(self)
        out = runner.run(*PACKAGES, down=RUNNER_HOSTS + ["mirror-one.example.org"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(runner.lines("installed")[-1].split(" from ")[1], "mirror-two.example.org")
        runner = Runner(self)
        out = runner.run(*PACKAGES, down=RUNNER_HOSTS, no_release=["mirror-one.example.org"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(runner.lines("installed")[-1].split(" from ")[1], "mirror-two.example.org")

    def test_a_second_install_in_the_same_job_goes_straight_to_the_mirror(self):
        # the step installs the wkhtmltox .deb after the archive packages
        runner = Runner(self)
        runner.run(*PACKAGES, down=RUNNER_HOSTS)
        before = len(runner.lines("getent"))
        out = runner.run("/tmp/wkhtmltox.deb", down=RUNNER_HOSTS)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(runner.lines("installed")[-1], "installed /tmp/wkhtmltox.deb from mirror-one.example.org")
        self.assertEqual(runner.lines("getent")[before:], ["getent mirror-one.example.org"])
        self.assertNotIn("does not resolve", out.stdout)

    def test_when_nothing_answers_the_step_fails_and_says_so(self):
        runner = Runner(self)
        out = runner.run(*PACKAGES, down=RUNNER_HOSTS + ["mirror-one.example.org", "mirror-two.example.org"])
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("::error::no Ubuntu archive answered from this runner", out.stdout)
        self.assertFalse(runner.lines("installed"))


class TestARealErrorStillFails(unittest.TestCase):
    def test_a_package_that_does_not_exist_fails_from_every_archive(self):
        runner = Runner(self)
        out = runner.run("mariadb-client", "no-such-package", missing=["no-such-package"])
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse(runner.lines("installed"))

    def test_without_a_package_the_helper_refuses_to_run(self):
        out = Runner(self).run()
        self.assertEqual(out.returncode, 2)


class TestTheWorkflowInstallsThroughTheHelper(unittest.TestCase):
    def setUp(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        start = text.index("- name: Install MariaDB client and wkhtmltopdf")
        end = text.index("- name: ", start + 10)
        self.step = [line.strip() for line in text[start:end].splitlines() if not line.strip().startswith("#")]

    def test_the_archive_packages_and_the_wkhtmltox_release_go_through_the_helper(self):
        self.assertIn("bash ~/ci_apt_install.sh mariadb-client xfonts-75dpi xfonts-base", self.step)
        self.assertTrue([line for line in self.step if "bash ~/ci_apt_install.sh /tmp/wkhtmltox.deb" in line])

    def test_no_bare_apt_call_is_left_in_the_step(self):
        # only the fallback written when the helper itself cannot be fetched may call apt-get directly
        bare = [line for line in self.step if "apt-get" in line and "ci_apt_install.sh" not in line]
        self.assertEqual(bare, [])


if __name__ == "__main__":
    unittest.main()
