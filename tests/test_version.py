"""The version is written down twice, so assert the two agree.

``pyproject.toml`` is what PyPI publishes; ``billkit/_version.py`` is what
``_transport.py`` puts in the User-Agent. Nothing derives one from the other:
reading the installed metadata would break a plain source checkout, which is how
the integration suite runs.

The release gate only checks the tag against ``pyproject.toml``, so without this
a bump that misses ``_version.py`` publishes cleanly and then reports the wrong
version on every request for the life of the release.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from billkit import __version__

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_version_matches_pyproject() -> None:
    declared = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["version"]
    assert __version__ == declared


def test_user_agent_reports_the_same_version() -> None:
    from billkit._transport import _user_agent

    assert re.fullmatch(rf"billkit-python/{re.escape(__version__)} httpx/.+", _user_agent())
