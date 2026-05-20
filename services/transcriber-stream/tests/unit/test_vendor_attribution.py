"""Vendor NOTICE drift test (design v7 XC-3).

Parses ``services/transcriber-stream/NOTICE`` and
``services/transcriber-stream/src/panakoes_transcriber_stream/vendor/README.md``
and confirms that the numbered modification list in both files agrees.

If a future agent edits the vendored code without updating both files,
this test fails the build. Trivial, but binding-contract enforcement.
"""

from __future__ import annotations

import os
import re

# Compile-once: matches ``  1. some text`` style numbered list headings.
# We only use this to locate entry-start positions; the body of each
# entry runs until the next match or until the next blank-then-non-list
# block (handled by ``_extract_mod_entries`` below).
_MOD_HEAD = re.compile(r"^\s{0,3}(\d+)\.\s+(.*)$", re.MULTILINE)


def _service_root() -> str:
    """Absolute path to ``services/transcriber-stream``."""

    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _normalize(text: str) -> str:
    """Collapse whitespace + strip markup so the comparison is robust."""

    # Drop markdown emphasis and backticks; both files are free to apply
    # their own typography as long as the prose agrees.
    text = text.replace("`", "").replace("**", "").replace("*", "")
    text = re.sub(r"\s+", " ", text).strip().rstrip(".").lower()
    return text


def _extract_mod_entries(content: str, section_token: str) -> dict[int, str]:
    """Extract the numbered modifications under a section heading.

    Each modification's body is the text from its ``N.`` heading line up
    to (but not including) the next numbered heading at the same depth.
    Once we see a number we have already recorded, we assume we have hit
    a different numbered list (e.g. the ``Bump procedure`` list in
    vendor/README.md) and stop.
    """

    start = content.find(section_token)
    if start < 0:
        raise AssertionError(f"section heading {section_token!r} missing")
    section = content[start + len(section_token) :]

    # Bound the section: stop at the next ``##`` markdown heading OR at
    # a line of ``===`` underline (NOTICE's section separator).
    section_end_match = re.search(r"(?m)^(##\s|====)", section)
    if section_end_match is not None:
        section = section[: section_end_match.start()]

    matches = list(_MOD_HEAD.finditer(section))
    entries: dict[int, str] = {}
    for idx, match in enumerate(matches):
        number = int(match.group(1))
        if number in entries:
            # Hit the second occurrence of a number -> we are in a
            # different numbered list. Stop.
            break
        body_start = match.start(2)
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(section)
        body = section[body_start:body_end].strip()
        entries[number] = body
    return entries


def test_vendor_notice_and_readme_modifications_agree() -> None:
    root = _service_root()
    notice_path = os.path.join(root, "NOTICE")
    vendor_readme = os.path.join(root, "src", "panakoes_transcriber_stream", "vendor", "README.md")

    notice_mods = _extract_mod_entries(
        _read(notice_path), "Modifications (cross-reference vendor/README.md):"
    )
    readme_mods = _extract_mod_entries(_read(vendor_readme), "## Modifications")

    # Both files must enumerate the same modification numbers.
    assert set(notice_mods.keys()) == set(readme_mods.keys()), (
        f"NOTICE has mods {sorted(notice_mods)} but vendor README has "
        f"{sorted(readme_mods)}; drift detected. Update both files so the "
        f"modification numbers agree exactly."
    )

    # Per-mod text must agree (normalized).
    mismatched: list[int] = []
    for number, notice_body in notice_mods.items():
        readme_body = readme_mods[number]
        if _normalize(notice_body) != _normalize(readme_body):
            mismatched.append(number)

    assert not mismatched, (
        "Vendor attribution drift on modifications "
        + ", ".join(str(n) for n in mismatched)
        + ". NOTICE and vendor/README.md disagree on these entries; fix both."
    )


def test_vendor_readme_lists_required_mod_numbers() -> None:
    """Defense: the lift MUST keep mods 1, 3, 4, 5, 6, 7 documented.

    Modification #2 is reserved (no current change); the others are
    binding-contract per design v7 line 33-39. If a bump procedure
    accidentally drops a mod, this test catches it before merge.
    """

    root = _service_root()
    vendor_readme = os.path.join(root, "src", "panakoes_transcriber_stream", "vendor", "README.md")
    readme_mods = _extract_mod_entries(_read(vendor_readme), "## Modifications")
    for required in (1, 3, 4, 5, 6, 7):
        assert required in readme_mods, (
            f"vendor README is missing required modification #{required}; "
            f"design v7 line 33-39 makes this binding."
        )
