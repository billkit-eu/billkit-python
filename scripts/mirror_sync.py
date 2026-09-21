#!/usr/bin/env python3
"""Regenerate ``resources.py``'s sync half from its async half.

``billkit.resources`` ships every resource twice: ``AsyncCustomers`` and
``Customers``, ``AsyncInvoices`` and ``Invoices``, and so on. The two are
a mirror — same methods, same signatures, same docstrings, same bodies,
differing only in ``async``/``await``, ``AsyncIterator`` vs ``Iterator``,
and ``aiterate`` vs ``paginate``.

That mirror used to be maintained by hand, and it drifted in both ways
that a hand-maintained mirror drifts:

* **Silently, in behaviour.** ``Customers.list`` gained a ``provisional``
  filter on the async class and never got it on the sync one, so the
  client most Python callers reach for could not ask the question at all.
* **Loudly, in documentation.** Twenty-eight methods and eleven classes
  explained themselves in full on the async side and said one line, or
  nothing, on the sync side.

So the sync half is generated. Edit only the async classes; run this to
propagate. CI runs ``--check``, which fails on any drift and prints the
diff, so the two cannot come apart again between reviews.

    python scripts/mirror_sync.py           # rewrite the sync half
    python scripts/mirror_sync.py --check   # fail if it is out of date

This is the ``unasync`` idea (httpx, urllib3, elasticsearch-py all do a
version of it) kept deliberately small: one file, one direction, textual
substitutions over a marked region. There is no import to rewrite and no
second module to publish, so the public surface is exactly what it was.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import subprocess
import sys
from pathlib import Path

RESOURCES = Path(__file__).resolve().parents[1] / "src" / "billkit" / "resources.py"

ASYNC_MARKER = "# ─── Async resources ───"
SYNC_MARKER = "# ─── Sync resources ────"

#: Header written above the generated region, so the next person to open
#: the file in an editor learns this before they edit it.
SYNC_HEADER = """# ─── Sync resources ────────────────────────────────────────────────
#
# GENERATED from the async classes above by `scripts/mirror_sync.py`.
# Do not edit this region by hand: run the script instead, or your change
# is reverted the next time anyone does. `--check` runs in CI.
"""

#: Substitutions applied to the async source, in order. Each is anchored
#: on a word boundary so a longer identifier that merely contains one of
#: these is left alone.
SUBSTITUTIONS: list[tuple[str, str]] = [
    (r"\basync def\b", "def"),
    (r"\bawait ", ""),
    (r"\bAsyncIterator\b", "Iterator"),
    (r"\b_AsyncRequester\b", "_SyncRequester"),
    (r"\baiterate\(", "paginate("),
    # Class names, and the cross-references in docstrings that point at
    # them: a sync docstring saying ":class:`AsyncPrices`" sends the
    # reader to the wrong half.
    (r"\bAsync([A-Z]\w*)", r"\1"),
]


def render_sync(async_source: str) -> str:
    out = async_source
    for pattern, replacement in SUBSTITUTIONS:
        out = re.sub(pattern, replacement, out)
    return out


def split(text: str) -> tuple[str, str, str]:
    """Return (prefix, async region, sync region)."""
    a = text.index(ASYNC_MARKER)
    s = text.index(SYNC_MARKER)
    if s < a:
        raise SystemExit("resources.py: the sync marker must follow the async one.")
    return text[:a], text[a:s], text[s:]


def build(text: str) -> str:
    prefix, async_region, _ = split(text)
    body = async_region[len(ASYNC_MARKER) :]
    # Drop the async marker's own trailing rule characters and blank line;
    # the generated region carries its own header.
    body = body.split("\n", 1)[1].lstrip("\n")
    return reformat(prefix + async_region + SYNC_HEADER + "\n" + render_sync(body))


def reformat(source: str) -> str:
    """Run the result through ruff format.

    Dropping ``async ``/``await `` shortens lines, so a signature the
    formatter wrapped on the async side fits on one line here. Without
    this the generated half would be correct and permanently unformatted,
    and ``--check`` would report drift the generator itself had caused.
    """
    ruff = shutil.which("ruff")
    if ruff is None:  # pragma: no cover - only when the dev deps are absent
        raise SystemExit(
            "ruff is required to generate the sync half (it reflows the "
            "signatures that shorten). Install the dev dependencies."
        )
    done = subprocess.run(
        [ruff, "format", "--stdin-filename", str(RESOURCES), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:  # pragma: no cover - a syntax error in the output
        raise SystemExit(f"ruff format rejected the generated source:\n{done.stderr}")
    return done.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero (with a diff) if the sync half is out of date",
    )
    args = parser.parse_args()

    current = RESOURCES.read_text()
    expected = build(current)

    if current == expected:
        return 0
    if not args.check:
        RESOURCES.write_text(expected)
        print(f"rewrote the sync half of {RESOURCES.name}")
        return 0

    diff = difflib.unified_diff(
        current.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile="resources.py (on disk)",
        tofile="resources.py (generated from the async half)",
    )
    sys.stdout.writelines(diff)
    print(
        "\nThe sync resource classes no longer mirror the async ones.\n"
        "Edit the async half only, then run: python scripts/mirror_sync.py",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
