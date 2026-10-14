"""Generate ``src/billkit/types.py`` from the public OpenAPI document.

The response models, the open-enum constants and the typed webhook events
are all derived from one file: ``docs-public/public/openapi/latest.json``,
the contract the API publishes and diffs against its frozen baseline in CI.
Nothing in ``types.py`` is edited by hand; change the spec (or this script)
and regenerate:

    uv run python scripts/generate_models.py           # rewrite types.py
    uv run python scripts/generate_models.py --check   # fail on drift

``tests/test_generated_models.py`` runs ``--check`` so a spec change that
was not regenerated, or a hand edit to ``types.py``, fails the unit suite.
Outside the monorepo (the published mirror) the spec is not present and
that test skips; ``BILLKIT_OPENAPI_SPEC`` points it at a copy.

Why a small script rather than datamodel-code-generator: the output has
three properties a general tool does not give together. Every model
derives from ``BillKitObject`` (attribute *and* mapping access); every open
enum (``x-extensible-enum``) is a plain ``str`` field with its known values
exposed as a ``StrEnum`` for comparison only, so an unknown value can never
fail; and each webhook event is a subclass of ``Event`` with ``type`` as a
``Literal``, so ``isinstance`` and ``match`` both narrow ``data``.

Rules, all of them deliberate:

* A property in ``required`` is declared without a default; anything else
  is ``X | None = None``. Responses are never validated (see
  ``billkit._models``), so the annotations describe the contract rather
  than enforce it.
* ``{"type": "object"}`` with free-form ``additionalProperties`` is
  ``dict[str, Any]``; with string values it is ``dict[str, str]``.
* Request bodies are not generated. Methods take keyword arguments.
* A property whose name would shadow something every model already has
  (``items``, ``keys``, ``values``, ``get``, ``to_dict``, ``copy``,
  ``dict``, ``json``, any other Pydantic ``BaseModel`` attribute, any
  ``model_*`` name) or that is a Python keyword gets a trailing underscore
  as its attribute and keeps the wire name as its alias: ``obj.items_``,
  ``obj["items"]``. The mapping view and ``to_dict()`` always use the wire
  name. See :func:`attribute_name`.
"""

from __future__ import annotations

import argparse
import difflib
import json
import keyword
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PACKAGE_ROOT / "src" / "billkit" / "types.py"
DEFAULT_SPEC = PACKAGE_ROOT.parents[1] / "docs-public" / "public" / "openapi" / "latest.json"

#: Response schemas the SDK has no method for. Console-only routes are not
#: listed here: they are kept out of the public document itself
#: (``UNPUBLISHED_ROUTES`` server-side), so the spec is the filter.
SKIP_SCHEMAS = {"PingResponse"}

#: Python class names that do not follow the "drop the Response suffix" rule.
RENAMES = {
    "EventResponse": "Event",
    "CouponValidateResponse": "CouponValidation",
    "BillingPortalSessionRevokeResponse": "BillingPortalSessionRevocation",
    "CustomerStatsInfo": "CustomerStats",
    "ExpandedCustomer": "CustomerSummary",
    "ExpandedPrice": "PriceSummary",
    "ExpandedSubscription": "SubscriptionSummary",
    "RefundEligibilityView": "RefundEligibility",
    "PortalTenantTheme-Output": "PortalTenantTheme",
    "InvoiceEmailResponse": "InvoiceEmail",
}

#: Enum class names for open-enum fields, keyed by (schema, property). A
#: field not listed here is named ``<Model><Property>``. Identical names
#: must carry identical value sets; the generator refuses otherwise.
ENUM_NAMES = {
    ("CheckoutSessionResponse", "payment_method"): "PaymentMethod",
    ("OneShotPaymentResponse", "payment_method"): "PaymentMethod",
    ("DunningNotification", "payment_method"): "PaymentMethod",
    ("PaymentResponse", "payment_method"): "PaymentMethod",
    ("ExpandedPrice", "payment_methods"): "PaymentMethod",
    ("PriceResponse", "payment_methods"): "PaymentMethod",
    ("CheckoutSessionResponse", "ui_mode"): "UiMode",
    ("OneShotPaymentResponse", "ui_mode"): "UiMode",
    ("CouponResponse", "discount_type"): "DiscountType",
    ("CouponValidateResponse", "discount_type"): "DiscountType",
    ("CouponResponse", "duration"): "CouponDuration",
    ("CouponValidateResponse", "duration"): "CouponDuration",
    ("ExpandedSubscription", "status"): "SubscriptionStatus",
    ("ExpandedPrice", "billing_scheme"): "BillingScheme",
    ("PriceResponse", "billing_scheme"): "BillingScheme",
    ("ExpandedPrice", "interval"): "PriceInterval",
    ("PriceResponse", "interval"): "PriceInterval",
    ("ExpandedPrice", "tax_behavior"): "TaxBehavior",
    ("PriceResponse", "tax_behavior"): "TaxBehavior",
    ("ExpandedPrice", "tiers_mode"): "TiersMode",
    ("PriceResponse", "tiers_mode"): "TiersMode",
    ("ExpandedPrice", "usage_type"): "UsageType",
    ("PriceResponse", "usage_type"): "UsageType",
    ("PriceResponse", "refund_on_cancel"): "RefundOnCancel",
    ("TenantProviderCredentialResponse", "mode"): "Mode",
    ("SubscriptionResponse", "renewal_state"): "RenewalState",
    ("SubscriptionResponse", "cancellation_reason"): "CancellationReason",
    ("PlanChangePreviewResponse", "refusal_reason"): "PlanChangeRefusalReason",
    ("ErrorDetail", "type"): "ErrorType",
    ("ErrorDetail", "code"): "ErrorCode",
    ("ErrorDetail", "reason"): "ErrorReason",
}

#: Docstrings the spec does not carry, or carries for a different reader.
CLASS_DOCS = {
    "EventResponse": (
        "An event, as ``events.retrieve`` returns it and a webhook delivers it.\n\n"
        "An event of a type this SDK version knows is built as its subclass, for\n"
        "example :class:`SubscriptionUpdatedEvent`, whose ``data`` is typed. Any\n"
        "other type is a plain ``Event`` whose ``data`` is a\n"
        ":class:`billkit.BillKitObject`, so a newer event never fails to parse.\n"
        "Branch with ``isinstance`` (or ``match``) and ignore what you do not handle."
    ),
}
FIELD_DOCS = {
    ("EventResponse", "data"): (
        "The object the event is about, as it stood when the event was recorded."
        " At the top level: there is no ``data.object`` wrapper."
    ),
    ("EventResponse", "previous_attributes"): (
        "On an update, the fields of ``data`` that changed, with their values"
        " before the change. Always present, and ``None`` when nothing changed."
    ),
}

#: Open enums with a hand-maintained home elsewhere. ``EventType`` lives in
#: ``billkit.event_types`` because the API's own test suite parses it.
ENUMS_ELSEWHERE = {("EventResponse", "type")}

#: Schemas generated for their enums only, never as a model.
ENUM_ONLY_SCHEMAS = {"ErrorDetail"}

HEADER = '''"""Response models, open-enum constants and webhook event types.

GENERATED by ``scripts/generate_models.py`` from the public OpenAPI
document. Do not edit by hand: regenerate, and ``tests/test_generated_models.py``
fails until you do.

Every model is a :class:`billkit.BillKitObject`: read a field as an
attribute (``sub.status``) or as a key (``sub["status"]``,
``sub.get("status")``). Responses are not validated, so a field or value
this version does not know never raises; an unknown field is still
readable both ways.

Enum classes list the values this SDK version knows. The fields they
describe are typed ``str`` because every enum the API sends is open: treat
an unlisted value as "something newer", never as an error. Compare with
the constants, which are ``StrEnum`` members and equal the plain strings::

    if sub.status == SubscriptionStatus.PAST_DUE:
        ...
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from billkit._models import BillKitObject
'''


#: Names a spec property cannot take as an attribute, because every model
#: already has an attribute of that name: the mapping methods
#: :class:`billkit.BillKitObject` adds, and everything on Pydantic's
#: ``BaseModel`` (``copy``, ``dict``, ``json``, ``model_dump`` ...).
#: ``tests/test_models.py`` checks this covers every public attribute of
#: ``BillKitObject``, so a method added there cannot be shadowed silently.
RESERVED_ATTRIBUTES = frozenset(
    {"items", "keys", "values", "get", "to_dict"}
    | {name for name in dir(BaseModel) if not name.startswith("_")}
)


def attribute_name(prop: str) -> str:
    """The Python attribute a spec property is declared as.

    The wire name itself, unless it would shadow a model method or is a
    keyword: then the wire name plus ``_``, declared with the wire name as
    its alias so construction, ``obj["items"]`` and ``to_dict()`` still use
    the wire name.
    """
    # Soft keywords (``type``, ``match``) are ordinary identifiers.
    if prop in RESERVED_ATTRIBUTES or prop.startswith("model_") or keyword.iskeyword(prop):
        return prop + "_"
    return prop


def class_name(schema: str) -> str:
    if schema in RENAMES:
        return RENAMES[schema]
    name = re.sub(r"Response$", "", schema)
    return name.replace("-Output", "").replace("-Input", "").lstrip("_")


def camel(value: str) -> str:
    return "".join(
        part[:1].upper() + part[1:] for part in re.split(r"[^0-9A-Za-z]+", value) if part
    )


def member_name(value: str) -> str:
    name = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").upper()
    if not name or name[0].isdigit():
        name = "V_" + name
    return name


def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


class Generator:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.schemas: dict[str, Any] = spec["components"]["schemas"]
        #: enum class name -> (values, first owner) in discovery order
        self.enums: dict[str, tuple[tuple[str, ...], str]] = {}
        self.enum_docs: dict[str, list[str]] = {}
        #: Whether any property was renamed, so ``pydantic.Field`` is imported.
        self.uses_field = False

    # -- discovery -------------------------------------------------------

    def roots(self) -> list[str]:
        out: list[str] = []
        for ops in self.spec["paths"].values():
            for method, op in ops.items():
                if method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                for code, response in op.get("responses", {}).items():
                    if not code.startswith("2"):
                        continue
                    media = response.get("content", {}).get("application/json")
                    ref = (media or {}).get("schema", {}).get("$ref")
                    if ref:
                        out.append(ref_name(ref))
        for hooks in self.spec.get("webhooks", {}).values():
            for op in hooks.values():
                media = op.get("requestBody", {}).get("content", {}).get("application/json", {})
                ref = media.get("schema", {}).get("$ref")
                if ref:
                    out.append(ref_name(ref))
        out.append("ErrorDetail")
        return out

    def reachable(self) -> list[str]:
        seen: set[str] = set()
        stack = [r for r in self.roots() if r not in SKIP_SCHEMAS]
        # ``ListResponse_X_`` envelopes are ``ListObject[X]``; only X is generated.
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            for ref in self._refs(self.schemas[name]):
                if ref not in seen:
                    stack.append(ref)
        return sorted(seen)

    def _refs(self, node: Any) -> Iterable[str]:
        if isinstance(node, dict):
            if "$ref" in node:
                yield ref_name(node["$ref"])
            for value in node.values():
                yield from self._refs(value)
        elif isinstance(node, list):
            for value in node:
                yield from self._refs(value)

    # -- typing ----------------------------------------------------------

    def enum_for(self, schema: str, prop: str, values: list[str], description: str | None) -> str:
        name = ENUM_NAMES.get((schema, prop)) or class_name(schema) + camel(prop)
        known = tuple(values)
        if name in self.enums:
            if self.enums[name][0] != known:
                raise SystemExit(
                    f"enum {name} has two value sets ({schema}.{prop}); name them apart in ENUM_NAMES"
                )
        else:
            self.enums[name] = (known, f"{class_name(schema)}.{prop}")
        self.enum_docs.setdefault(name, [])
        owner = f"``{class_name(schema)}.{prop}``"
        if owner not in self.enum_docs[name]:
            self.enum_docs[name].append(owner)
        return name

    def _enum_of(self, schema: str, prop: str, node: dict[str, Any]) -> str | None:
        """The enum class a property's values are listed in, if any."""
        for candidate in [node, *node.get("anyOf", []), node.get("items", {})]:
            if "x-extensible-enum" in candidate:
                if (schema, prop) in ENUMS_ELSEWHERE:
                    return "billkit.EventType"
                return ENUM_NAMES.get((schema, prop)) or class_name(schema) + camel(prop)
        return None

    def annotation(self, schema: str, prop: str, node: dict[str, Any]) -> tuple[str, bool]:
        """(python annotation, nullable) for one property node."""
        if "anyOf" in node:
            parts = [p for p in node["anyOf"] if p.get("type") != "null"]
            nullable = len(parts) != len(node["anyOf"])
            if len(parts) != 1:
                raise SystemExit(f"{schema}.{prop}: unions other than X | null are not supported")
            inner, _ = self.annotation(schema, prop, {**parts[0]})
            return inner, nullable
        if "$ref" in node:
            return class_name(ref_name(node["$ref"])), False
        kind = node.get("type")
        if "x-extensible-enum" in node and (schema, prop) not in ENUMS_ELSEWHERE:
            self.enum_for(schema, prop, node["x-extensible-enum"], node.get("description"))
            return "str", False
        if kind == "string":
            return "str", False
        if kind == "integer":
            return "int", False
        if kind == "number":
            return "float", False
        if kind == "boolean":
            return "bool", False
        if kind == "array":
            item, item_nullable = self.annotation(schema, prop, node.get("items", {}))
            return f"list[{item} | None]" if item_nullable else f"list[{item}]", False
        if kind == "object" or kind is None:
            extra = node.get("additionalProperties")
            if isinstance(extra, dict) and extra.get("type") == "string":
                return "dict[str, str]", False
            return "dict[str, Any]", False
        raise SystemExit(f"{schema}.{prop}: unsupported schema {node!r}")

    # -- rendering -------------------------------------------------------

    def render_model(
        self, schema: str, *, base: str = "BillKitObject", only: set[str] | None = None
    ) -> str:
        node = self.schemas[schema]
        name = class_name(schema)
        required = set(node.get("required", []))
        lines = [f"class {name}({base}):"]
        object_const = node.get("properties", {}).get("object", {}).get("const")
        if object_const:
            article = "An" if object_const[0] in "aeiou" else "A"
            fallback = f"{article} ``{object_const}`` object."
        else:
            fallback = f"The ``{name}`` object."
        doc = CLASS_DOCS.get(schema) or node.get("description") or fallback
        lines += _docstring(doc, indent=4)
        props: dict[str, Any] = node.get("properties", {})
        attributes: set[str] = set()
        # ``id`` and ``object`` lead, as on the wire; the rest is alphabetical.
        for prop in sorted(props, key=lambda p: (p not in ("id", "object"), p != "id", p)):
            if only is not None and prop not in only:
                continue
            if not prop.isidentifier() or prop.startswith("_"):
                raise SystemExit(f"{schema}.{prop}: not usable as a field name")
            attr = attribute_name(prop)
            if attr in attributes or (attr != prop and attr in props):
                raise SystemExit(f"{schema}.{prop}: attribute {attr!r} is taken twice")
            attributes.add(attr)
            pnode = props[prop]
            if schema.endswith("Event") and schema != "EventResponse" and prop == "type":
                ann, nullable = f'Literal["{pnode["const"]}"]', False
            elif schema == "EventResponse" and prop == "data":
                ann, nullable = "BillKitObject", False
            else:
                ann, nullable = self.annotation(schema, prop, pnode)
            if prop in required:
                ann = f"{ann} | None" if nullable else ann
                if attr == prop:
                    lines.append(f"    {prop}: {ann}")
                else:
                    self.uses_field = True
                    lines.append(f'    {attr}: {ann} = Field(alias="{prop}")')
            elif attr == prop:
                lines.append(f"    {prop}: {ann} | None = None")
            else:
                self.uses_field = True
                lines.append(f'    {attr}: {ann} | None = Field(default=None, alias="{prop}")')
            doc = FIELD_DOCS.get((schema, prop)) or pnode.get("description") or ""
            enum = self._enum_of(schema, prop, pnode)
            if enum:
                note = f"Known values: :class:`{enum}`. Open, so expect others."
                doc = f"{doc} {note}" if doc else note
            if doc:
                lines += _docstring(doc, indent=4)
        return "\n".join(lines)

    def render_enum(self, name: str) -> str:
        values, _ = self.enums[name]
        owners = ", ".join(self.enum_docs[name])
        lines = [f"class {name}(StrEnum):"]
        lines += _docstring(
            f"Known values of {owners}. Open: the API can send a value not listed here.",
            indent=4,
        )
        for value in values:
            lines.append(f'    {member_name(value)} = "{value}"')
        return "\n".join(lines)

    def generate(self) -> str:
        names = [n for n in self.reachable() if not n.startswith("ListResponse_")]
        events = sorted(n for n in names if n.endswith("Event"))
        models = [n for n in names if n not in events and n not in ENUM_ONLY_SCHEMAS]
        for n in ENUM_ONLY_SCHEMAS:
            self.render_model(n)  # registers its enums; the model itself is not emitted

        ordered = _topological(models, {m: set(self._refs(self.schemas[m])) for m in models})
        model_src = [self.render_model(m) for m in ordered]

        event_src: list[str] = []
        event_map: list[str] = []
        for schema in events:
            node = self.schemas[schema]
            type_value = node["properties"]["type"]["const"]
            event_src.append(self.render_model(schema, base="Event", only={"type", "data"}))
            event_map.append(f'    "{type_value}": {class_name(schema)},')

        enum_src = [self.render_enum(n) for n in sorted(self.enums)]

        # Ruff's RUF022 order: SCREAMING_CASE, then CamelCase, then the rest.
        all_names = sorted(
            [class_name(m) for m in ordered]
            + [class_name(e) for e in events]
            + list(self.enums)
            + ["EVENT_CLASSES"],
            key=lambda n: (0 if n.isupper() else 1 if n[0].isupper() else 2, n),
        )

        header = HEADER
        if self.uses_field:
            header = header.replace(
                "from billkit._models import",
                "from pydantic import Field\n\nfrom billkit._models import",
            )
        parts = [header, "\n# ── Open-enum constants " + "─" * 49 + "\n"]
        parts += [src + "\n\n" for src in enum_src]
        parts.append("\n# ── Response models " + "─" * 52 + "\n")
        parts += [src + "\n\n" for src in model_src]
        parts.append("\n# ── Webhook events " + "─" * 53 + "\n")
        parts += [src + "\n\n" for src in event_src]
        parts.append(
            "\n#: Event ``type`` -> the class :meth:`billkit.WebhookSignature.verify` and\n"
            "#: ``events.retrieve`` return for it. A type not listed comes back as a\n"
            "#: plain :class:`Event` whose ``data`` is a :class:`billkit.BillKitObject`.\n"
            "EVENT_CLASSES: dict[str, type[Event]] = {\n" + "\n".join(event_map) + "\n}\n\n"
            "Event.__bk_variants__ = EVENT_CLASSES\n\n"
        )
        parts.append("__all__ = [\n" + "".join(f'    "{n}",\n' for n in all_names) + "]\n")
        return "".join(parts)


def _docstring(text: str, *, indent: int) -> list[str]:
    pad = " " * indent
    text = text.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if text.endswith('"'):
        text += " "
    body = text.splitlines()
    if len(body) == 1:
        return [f'{pad}"""{body[0]}"""']
    return (
        [f'{pad}"""{body[0]}']
        + [f"{pad}{line}" if line else "" for line in body[1:]]
        + [f'{pad}"""']
    )


def _topological(names: list[str], deps: dict[str, set[str]]) -> list[str]:
    out: list[str] = []
    done: set[str] = set()

    def visit(name: str, stack: tuple[str, ...]) -> None:
        if name in done:
            return
        if name in stack:
            raise SystemExit(f"schema cycle: {' -> '.join((*stack, name))}")
        for dep in sorted(deps.get(name, set())):
            if dep in deps:
                visit(dep, (*stack, name))
        done.add(name)
        out.append(name)

    for name in sorted(names):
        visit(name, ())
    return out


def render(spec_path: Path) -> str:
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    return _ruff_format(Generator(spec).generate())


def _ruff_format(source: str) -> str:
    # Prefer the ruff installed beside this interpreter (the project venv),
    # so the result does not depend on whichever ruff is first on PATH.
    ruff = shutil.which("ruff", path=str(Path(sys.executable).parent)) or shutil.which("ruff")
    if ruff is None:  # pragma: no cover - only when the dev deps are absent
        raise SystemExit("ruff is required to format the generated models.")
    done = subprocess.run(
        [ruff, "format", "--stdin-filename", str(OUTPUT), "-"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_ROOT,
    )
    if done.returncode != 0:  # pragma: no cover - a syntax error in the output
        raise SystemExit(f"ruff format rejected the generated source:\n{done.stderr}")
    return done.stdout


def spec_path() -> Path:
    override = os.environ.get("BILLKIT_OPENAPI_SPEC")
    return Path(override) if override else DEFAULT_SPEC


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail with a diff on drift")
    parser.add_argument("--spec", type=Path, default=None, help="path to the OpenAPI document")
    args = parser.parse_args()

    expected = render(args.spec or spec_path())
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
    if current == expected:
        return 0
    if not args.check:
        OUTPUT.write_text(expected, encoding="utf-8")
        print(f"wrote {OUTPUT.relative_to(PACKAGE_ROOT)}")
        return 0
    sys.stdout.writelines(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile="types.py (on disk)",
            tofile="types.py (generated from the spec)",
        )
    )
    print(
        "\ntypes.py is out of date. Run: uv run python scripts/generate_models.py", file=sys.stderr
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
