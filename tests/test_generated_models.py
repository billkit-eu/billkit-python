"""``types.py`` is exactly what the generator makes of the public spec.

Inside the monorepo the spec is ``docs-public/public/openapi/latest.json``.
The published mirror does not carry it, so these tests skip there unless
``BILLKIT_OPENAPI_SPEC`` points at a copy.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from billkit import BillKit, EventType
from billkit.types import EVENT_CLASSES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import generate_models  # noqa: E402

SPEC_PATH = generate_models.spec_path()
pytestmark = pytest.mark.skipif(
    not SPEC_PATH.exists(), reason=f"OpenAPI spec not found at {SPEC_PATH}"
)


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_regenerating_produces_no_diff() -> None:
    venv_bin = str(Path(sys.executable).parent)
    if shutil.which("ruff", path=venv_bin) is None and shutil.which("ruff") is None:
        pytest.skip("ruff is not installed; the generator formats its output with it")
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_models.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr


def test_spec_is_the_1_0_contract(spec: dict[str, Any]) -> None:
    assert spec["info"]["version"].startswith("1.")


def test_event_classes_match_the_spec_webhooks(spec: dict[str, Any]) -> None:
    assert set(EVENT_CLASSES) == set(spec["webhooks"])


def test_event_type_constants_match_the_spec(spec: dict[str, Any]) -> None:
    enum = spec["components"]["schemas"]["EventResponse"]["properties"]["type"]
    assert {e.value for e in EventType} == set(enum["x-extensible-enum"])


_LISTS = {"list", "list_usage_records", "list_deliveries"}


def test_list_filters_match_the_spec_query_parameters(spec: dict[str, Any]) -> None:
    """Every query parameter a list route takes is a keyword on the SDK
    method, and the SDK sends nothing the route does not take."""
    queries: dict[str, set[str]] = {}
    for path, ops in spec["paths"].items():
        get = ops.get("get")
        if get:
            norm = re.sub(r"\{[^}]+\}", "{}", path)
            queries[norm] = {p["name"] for p in get.get("parameters", []) if p["in"] == "query"}
    client = BillKit(api_key="bk_test_unit")
    checked = 0
    for attr, resource in vars(client).items():
        if attr.startswith("_"):
            continue
        for name in _LISTS & set(dir(resource)):
            method = getattr(resource, name)
            body = inspect.getsource(method)
            match = re.search(r'"(/v1/[^"]*)"|f"(/v1/[^"]*)"', body)
            assert match, (attr, name)
            path = re.sub(r"\{[^}]+\}", "{}", match.group(1) or match.group(2))
            params = {
                p.name
                for p in inspect.signature(method).parameters.values()
                if p.kind is inspect.Parameter.KEYWORD_ONLY
            } - {"timeout", "max_retries", "extra_headers", "extra_query"}
            assert params == queries[path], (attr, name, params ^ queries[path])
            checked += 1
    client.close()
    assert checked == 19
