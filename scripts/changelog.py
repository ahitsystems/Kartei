#!/usr/bin/env python3
"""Changelog entries as one file per change, so parallel pull requests don't collide in CHANGELOG.md.

A pull request adds changes/<short-name>.<type>.md with one or more bullet lines; <type> is one of the
Keep a Changelog sections: added, changed, deprecated, removed, fixed, security.

  python3 scripts/changelog.py check              every file in changes/ is valid (CI)
  python3 scripts/changelog.py preview            the next release's section, as it would be written
  python3 scripts/changelog.py release 1.2.0 [--date 2026-12-01]
      moves everything under "## [Unreleased]" plus all files in changes/ into a new "## [1.2.0] – date"
      section and deletes the files; then write its "New in this release" / "Upgrading" lines by hand.

Standard library only; the test is scripts/test_changelog.py.
"""
import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TYPES = ["added", "changed", "deprecated", "removed", "fixed", "security"]  # Keep a Changelog order
NAME = re.compile(r"^[a-z0-9][a-z0-9-]*\.(%s)\.md$" % "|".join(TYPES))
UNRELEASED = "## [Unreleased]"


def fragments(changes: Path):
    """[(path, type, bullet lines)] sorted by file name; problems as a list of messages"""
    out, problems = [], []
    for p in sorted(changes.glob("*.md")) if changes.is_dir() else []:
        if p.name == "README.md":
            continue
        m = NAME.match(p.name)
        text = p.read_text(encoding="utf-8").strip()
        if not m:
            problems.append(f"{p.name}: name must be <short-name>.<type>.md, type one of {', '.join(TYPES)}")
        elif not text:
            problems.append(f"{p.name}: empty")
        elif text.startswith("#"):
            problems.append(f"{p.name}: only bullet lines, the section heading comes from the file name")
        else:
            out.append((p, m.group(1), text if text.startswith("- ") else f"- {text}"))
    return out, problems


def split_unreleased(log: str):
    """(text before the section, its body, text from the next version on)"""
    start = log.index(UNRELEASED) + len(UNRELEASED)
    nxt = re.search(r"^## \[", log[start:], re.M)
    end = start + nxt.start() if nxt else len(log)
    return log[:start], log[start:end].strip(), log[end:]


def section(body: str, frags) -> str:
    """Unreleased body (free text, then ### sections) with the fragments merged into their sections"""
    parts = re.split(r"^### +(.+)$", body, flags=re.M)
    intro, found = parts[0].strip(), {}
    for title, text in zip(parts[1::2], parts[2::2]):
        found.setdefault(title.strip(), []).append(text.strip())
    for _, kind, text in frags:
        found.setdefault(kind.capitalize(), []).append(text)
    order = [t.capitalize() for t in TYPES] + [t for t in found if t.lower() not in TYPES]
    out = [intro] if intro else []
    out += [f"### {t}\n\n" + "\n".join(x for x in found[t] if x) for t in order if any(found.get(t, []))]
    return "\n\n".join(out)


def main(argv=None, root=ROOT):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("preview")
    rel = sub.add_parser("release")
    rel.add_argument("version", help="e.g. 1.2.0 or 1.2.0-beta (without the v of the tag)")
    rel.add_argument("--date", default=datetime.date.today().isoformat())
    args = ap.parse_args(argv)

    changes, log_path = root / "changes", root / "CHANGELOG.md"
    frags, problems = fragments(changes)
    if problems:
        print("changes/:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 1
    if args.cmd == "check":
        print(f"changes/: {len(frags)} entries, all valid")
        return 0
    head, body, rest = split_unreleased(log_path.read_text(encoding="utf-8"))
    text = section(body, frags)
    if args.cmd == "preview":
        print(text or "(nothing unreleased)")
        return 0
    version = args.version.removeprefix("v")
    if f"## [{version}]" in rest:
        print(f"CHANGELOG.md already has a section {version}", file=sys.stderr)
        return 1
    log_path.write_text(f"{head}\n\n## [{version}] – {args.date}\n\n" + (f"{text}\n\n" if text else "") + rest.lstrip("\n"),
                        encoding="utf-8")
    for p, _, _ in frags:
        p.unlink()
    print(f"CHANGELOG.md: section {version} with {len(frags)} entries from changes/; add the release intro by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
