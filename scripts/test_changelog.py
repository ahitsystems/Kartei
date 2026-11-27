"""python3 -m unittest discover -s scripts"""
import tempfile
import unittest
from pathlib import Path

import changelog

LOG = """# Changelog

Intro.

## [Unreleased]

### Fixed

- **Old way:** an entry written straight into CHANGELOG.md.

## [1.0.0] – 2026-01-01

### Added

- First.
"""


class ChangelogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        (self.root / "changes").mkdir()
        (self.root / "CHANGELOG.md").write_text(LOG, encoding="utf-8")
        self.addCleanup(self.dir.cleanup)

    def add(self, name, text):
        (self.root / "changes" / name).write_text(text, encoding="utf-8")

    def run_cmd(self, *argv):
        return changelog.main(list(argv), root=self.root)

    def test_release_collects_fragments_in_section_order(self):
        self.add("README.md", "how to")  # not an entry
        self.add("phone-view.added.md", "- **Phone view:** speed dial.\n")
        self.add("call-url.added.md", "**Incoming calls:** /call/<number>.")  # the "- " is added
        self.add("nav.changed.md", "- **Settings:** grouped navigation.\n- second line\n")
        self.assertEqual(self.run_cmd("check"), 0)
        self.assertEqual(self.run_cmd("release", "v1.1.0", "--date", "2026-12-01"), 0)
        log = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        new = log.split("## [1.1.0] – 2026-12-01\n\n")[1].split("## [1.0.0]")[0]
        self.assertEqual(new, "### Added\n\n- **Incoming calls:** /call/<number>.\n- **Phone view:** speed dial.\n\n"
                              "### Changed\n\n- **Settings:** grouped navigation.\n- second line\n\n"
                              "### Fixed\n\n- **Old way:** an entry written straight into CHANGELOG.md.\n\n")
        self.assertIn("## [Unreleased]\n\n## [1.1.0]", log)  # Unreleased is empty again
        self.assertEqual([p.name for p in (self.root / "changes").iterdir()], ["README.md"])
        self.assertEqual(self.run_cmd("release", "1.1.0"), 1)  # not twice

    def test_check_rejects_bad_files(self):
        self.add("typo.addded.md", "- x")
        self.assertEqual(self.run_cmd("check"), 1)
        (self.root / "changes" / "typo.addded.md").unlink()
        self.add("empty.fixed.md", "  \n")
        self.assertEqual(self.run_cmd("check"), 1)
        (self.root / "changes" / "empty.fixed.md").unlink()
        self.add("heading.fixed.md", "### Fixed\n- x")
        self.assertEqual(self.run_cmd("check"), 1)

    def test_nothing_unreleased(self):
        (self.root / "CHANGELOG.md").write_text(LOG.replace("### Fixed\n\n- **Old way:** an entry written straight "
                                                           "into CHANGELOG.md.\n", ""), encoding="utf-8")
        self.assertEqual(self.run_cmd("preview"), 0)
        self.assertEqual(self.run_cmd("release", "1.1.0", "--date", "2026-12-01"), 0)
        self.assertIn("## [Unreleased]\n\n## [1.1.0] – 2026-12-01\n\n## [1.0.0]",
                      (self.root / "CHANGELOG.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
