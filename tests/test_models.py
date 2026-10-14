"""The response models: both access styles, and forward compatibility.

A response must never fail because the API is newer than the SDK: an
unknown field, an unknown enum value, an unknown event type, or a value of
an unexpected type all have to come back readable rather than raise.
"""

from __future__ import annotations

import copy
import json
import pickle
import warnings
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
import respx

from billkit import AsyncBillKit, BillKit, BillKitObject, Event, ListObject
from billkit._models import construct
from billkit.types import (
    EVENT_CLASSES,
    Coupon,
    Customer,
    CustomerSummary,
    Invoice,
    InvoiceLineItem,
    PriceSummary,
    RenewalState,
    Subscription,
    SubscriptionStatus,
    SubscriptionUpdatedEvent,
)

SUB: dict[str, Any] = {
    "id": "sub_1",
    "object": "subscription",
    "created": 1_700_000_000,
    "livemode": False,
    "customer_id": "cus_1",
    "price_id": "price_1",
    "status": "active",
    "renewal_state": "auto_renew",
    "serves_customer": True,
    "current_period_start": 1_700_000_000,
    "current_period_end": 1_702_592_000,
    "cancel_at_period_end": False,
    "canceled_at": None,
    "cancellation_reason": None,
    "last_payment_id": "pay_1",
    "last_payment_at": 1_700_000_000,
    "payment_count": 1,
    "trial_start": None,
    "trial_end": None,
    "dunning_attempts_notified": 0,
    "dunning_last_notified_at": None,
    "next_payment_attempt": None,
    "discount": None,
    "payment_method": {"type": "ideal", "brand": None, "last4": None, "exp_month": None},
}


def sub(**overrides: Any) -> Subscription:
    return construct(Subscription, {**SUB, **overrides})


# ── both access styles ─────────────────────────────────────────────


def test_attribute_and_mapping_access_agree() -> None:
    s = sub()
    assert s.status == "active"
    assert s["status"] == "active"
    assert s.get("status") == "active"
    assert s.get("no_such_key") is None
    assert s.get("no_such_key", "fallback") == "fallback"
    assert "status" in s
    assert "no_such_key" not in s
    assert 1 not in s  # type: ignore[comparison-overlap]
    with pytest.raises(KeyError):
        s["no_such_key"]


def test_is_a_read_only_mapping() -> None:
    s = sub()
    assert isinstance(s, Mapping)
    assert isinstance(s, BillKitObject)
    assert not isinstance(s, dict)
    assert list(s)[:2] == ["id", "object"]
    assert set(s.keys()) == set(SUB)
    assert len(s) == len(SUB)
    assert dict(s.items())["price_id"] == "price_1"
    assert "price_1" in s.values()
    with pytest.raises(TypeError):
        s["status"] = "canceled"  # type: ignore[index]


def test_dict_and_to_dict() -> None:
    s = sub()
    shallow = dict(s)
    assert shallow["status"] == "active"
    # dict() is shallow: nested objects stay objects.
    assert isinstance(shallow["payment_method"], BillKitObject)
    # to_dict() is plain JSON all the way down, and exactly what was sent.
    assert s.to_dict() == SUB
    assert type(s.to_dict()["payment_method"]) is dict


def test_nested_objects_are_models() -> None:
    s = sub(
        customer={
            "id": "cus_1",
            "object": "customer.summary",
            "name": "Ada",
            "email": None,
            "country_code": "NL",
        }
    )
    assert isinstance(s.customer, CustomerSummary)
    assert s.customer.name == "Ada"
    assert s["customer"]["name"] == "Ada"
    assert s["customer"].get("email") is None


def test_declared_but_absent_reads_none_and_is_not_a_key() -> None:
    """The 0.x dict had no key for a field the API did not send; the model
    keeps that, and the attribute reads ``None`` rather than raising."""
    s = sub()
    assert "price" not in SUB
    assert s.price is None
    assert "price" not in s
    assert s.get("price") is None
    assert "price" not in s.to_dict()


def test_equality() -> None:
    assert sub() == sub()
    assert sub() != sub(status="past_due")
    # Never equal to a dict: compare to_dict() when that is what you mean.
    assert sub() != SUB
    assert sub().to_dict() == SUB


def test_equality_is_wire_equality() -> None:
    """Pydantic's own ``==`` ignores which keys were sent; the wire view
    does not, so ``"discount": null`` and an absent ``discount`` differ."""
    sent_null = sub()
    absent = construct(Subscription, {k: v for k, v in SUB.items() if k != "discount"})
    assert sent_null.discount is None
    assert absent.discount is None
    assert sent_null != absent
    assert construct(Customer, {"id": "c"}) != construct(CustomerSummary, {"id": "c"})


def test_mutable_attributes_like_other_payment_sdks() -> None:
    s = sub()
    s.status = "canceled"
    assert s["status"] == "canceled"


def test_copy_and_pickle_round_trip() -> None:
    # Pickle only ever reads bytes this test just produced; it checks that
    # models survive caches and process pools, not untrusted input.
    s = sub(new_top_level={"a": [1, 2]})
    assert copy.deepcopy(s) == s
    assert pickle.loads(pickle.dumps(s)) == s
    assert pickle.loads(pickle.dumps(s)).to_dict() == s.to_dict()


# ── forward compatibility ──────────────────────────────────────────


def test_unknown_fields_are_kept_and_readable_both_ways() -> None:
    s = sub(launched_in_2027={"region": "eu-north"}, flag=True)
    assert s["launched_in_2027"] == {"region": "eu-north"}
    assert s.launched_in_2027 == {"region": "eu-north"}  # type: ignore[attr-defined]
    assert s.flag is True  # type: ignore[attr-defined]
    assert "launched_in_2027" in s
    assert s.to_dict()["launched_in_2027"] == {"region": "eu-north"}


def test_unknown_enum_values_never_raise() -> None:
    s = sub(status="suspended_by_regulator", renewal_state="hibernating")
    assert s.status == "suspended_by_regulator"
    assert s.renewal_state == "hibernating"
    assert s.status != SubscriptionStatus.ACTIVE
    # Known values compare equal to the plain strings.
    assert sub().status == SubscriptionStatus.ACTIVE
    assert sub().renewal_state == RenewalState.AUTO_RENEW


def test_unexpected_types_are_kept_not_rejected() -> None:
    """Responses are not validated: a payments SDK must not turn a charge
    that happened into an exception because a field changed shape."""
    s = sub(payment_count="7", customer="cus_1", discount=[1, 2])
    assert s.payment_count == "7"  # type: ignore[comparison-overlap]
    assert s["customer"] == "cus_1"
    assert s.discount == [1, 2]


def test_missing_required_field_reads_none() -> None:
    data = dict(SUB)
    del data["status"]
    s = construct(Subscription, data)
    assert s.status is None
    assert "status" not in s


def test_unknown_nested_object_stays_plain_json() -> None:
    s = sub(future={"inner": {"deep": 1}})
    assert s["future"]["inner"]["deep"] == 1
    assert type(s["future"]) is dict


def test_coupon_fields_are_the_1_0_names() -> None:
    c = construct(
        Coupon,
        {
            "id": "co_1",
            "object": "coupon",
            "discount_type": "percent",
            "percent_off": 10,
            "amount_off_cents": None,
        },
    )
    assert c.percent_off == 10
    assert c.amount_off_cents is None
    assert "discount_value" not in Coupon.model_fields


@pytest.mark.parametrize(("wire", "printed"), [(99, "99"), (12.5, "12.5"), (100, "100")])
def test_percent_off_reads_back_whole_as_int_and_fractional_as_float(
    wire: float, printed: str
) -> None:
    """The API serialises a whole percent as an integer (``99``) and a
    fractional one as a number (``12.5``). Both read back as sent, with no
    Pydantic serialisation warning on the way out, and print the way the
    API wrote them rather than as ``99.0``."""
    body = json.loads(json.dumps({"id": "co_1", "object": "coupon", "percent_off": wire}))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        c = construct(Coupon, body)
        assert c.percent_off == wire
        assert type(c.percent_off) is type(wire)
        assert f"{c.percent_off}% off" == f"{printed}% off"
        assert c.to_dict()["percent_off"] == wire
        assert json.loads(c.model_dump_json())["percent_off"] == wire
        assert c.model_dump()["percent_off"] == wire


def test_price_summary_and_lists_of_models() -> None:
    inv = construct(
        Invoice,
        {
            "id": "in_1",
            "object": "invoice",
            "lines": [{"id": "il_1", "object": "invoice_line_item", "amount_cents": 100}],
        },
    )
    lines = inv["lines"]
    assert isinstance(lines, list)
    assert isinstance(lines[0], BillKitObject)
    assert isinstance(inv.lines[0], InvoiceLineItem)
    assert inv.lines[0].amount_cents == 100
    assert PriceSummary.model_fields["unit_amount_decimal"].annotation is not None


# ── ListObject ─────────────────────────────────────────────────────


def test_list_object_iterates_items_and_keeps_the_envelope() -> None:
    page = construct(
        ListObject[Subscription],
        {"object": "list", "data": [SUB, {**SUB, "id": "sub_2"}], "has_more": True},
    )
    assert [s.id for s in page] == ["sub_1", "sub_2"]
    assert len(page) == 2
    assert isinstance(page.data[0], Subscription)
    assert page["has_more"] is True
    assert page.get("object") == "list"
    assert page["data"][1].id == "sub_2"
    assert page.get("no_such_key", "fallback") == "fallback"
    assert list(page.to_dict()) == ["object", "data", "has_more"]
    assert page.to_dict()["data"][1]["id"] == "sub_2"


def test_list_object_is_a_sequence_of_items_not_a_mapping() -> None:
    """A Mapping must iterate its keys; a page iterates its items, so it is
    not registered as one (``for k in m: m[k]`` would fail)."""
    page = construct(ListObject[Subscription], {"object": "list", "data": [SUB], "has_more": False})
    assert not isinstance(page, Mapping)
    item = page.data[0]
    assert item in page
    assert sub(id="sub_other") not in page
    # A string asks about the envelope, as in 0.x.
    assert "has_more" in page
    assert "no_such_key" not in page
    with pytest.raises(KeyError):
        page["no_such_key"]
    with pytest.raises(TypeError, match=r"page\.data\[i\]"):
        page[0]  # type: ignore[index]
    with pytest.raises(TypeError, match=r"page\.data\[i\]"):
        page.get(0)  # type: ignore[arg-type]


def test_empty_page_is_still_truthy() -> None:
    """A 0.x page was a non-empty dict, so ``if page:`` was always true."""
    page = construct(ListObject[Subscription], {"object": "list", "data": [], "has_more": False})
    assert len(page) == 0
    assert bool(page) is True


# ── events ─────────────────────────────────────────────────────────


def event(type_: str, data: dict[str, Any], previous: dict[str, Any] | None = None) -> Event:
    return construct(
        Event,
        {
            "id": "evt_1",
            "object": "event",
            "type": type_,
            "created": 1,
            "livemode": False,
            "data": data,
            "previous_attributes": previous,
        },
    )


def test_known_event_type_is_its_subclass_with_typed_data() -> None:
    e = event("subscription.updated", SUB, {"renewal_state": "auto_renew"})
    assert isinstance(e, SubscriptionUpdatedEvent)
    assert isinstance(e, Event)
    assert isinstance(e.data, Subscription)
    assert e.data.renewal_state == "auto_renew"
    assert e.previous_attributes == {"renewal_state": "auto_renew"}
    assert e["data"]["status"] == "active"


def test_every_event_class_is_dispatched() -> None:
    for type_, cls in EVENT_CLASSES.items():
        assert type(event(type_, {"id": "x"})) is cls


def test_unknown_event_type_is_a_plain_event() -> None:
    e = event("crypto.mined", {"id": "x", "hash": "abc", "nested": {"k": 1}})
    assert type(e) is Event
    assert type(e.data) is BillKitObject
    assert e.data["hash"] == "abc"
    assert e.data.hash == "abc"  # type: ignore[attr-defined]
    assert e.data.get("nested") == {"k": 1}
    assert e.type == "crypto.mined"


def test_previous_attributes_null_is_none_and_present() -> None:
    e = event("customer.created", {"id": "cus_1"}, None)
    assert e.previous_attributes is None
    assert "previous_attributes" in e
    assert e.to_dict()["previous_attributes"] is None


def test_building_a_specific_event_class_directly_does_not_redispatch() -> None:
    e = construct(SubscriptionUpdatedEvent, {"type": "invoice.paid", "data": SUB})
    assert type(e) is SubscriptionUpdatedEvent


def test_match_statement_narrows() -> None:
    e = event("subscription.updated", SUB)
    match e:
        case SubscriptionUpdatedEvent(data=Subscription(status=status)):
            assert status == "active"
        case _:
            pytest.fail("did not match")


# ── through the client ─────────────────────────────────────────────


@respx.mock
def test_sync_client_returns_models(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/subscriptions/sub_1").mock(
        return_value=httpx.Response(200, json={**SUB, "brand_new": 1})
    )
    s = sync_client.subscriptions.retrieve("sub_1")
    assert isinstance(s, Subscription)
    assert s.status == s["status"] == s.get("status") == "active"
    assert s["brand_new"] == 1


@respx.mock
async def test_async_client_returns_models(async_client: AsyncBillKit) -> None:
    respx.get("https://test.billkit.eu/v1/events/evt_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt_1",
                "object": "event",
                "type": "subscription.updated",
                "created": 1,
                "livemode": False,
                "data": SUB,
                "previous_attributes": None,
            },
        )
    )
    e = await async_client.events.retrieve("evt_1")
    assert isinstance(e, SubscriptionUpdatedEvent)
    assert e.data.id == "sub_1"


@respx.mock
def test_a_success_body_that_is_not_an_object_raises_naming_success(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, json=[1, 2])
    )
    with pytest.raises(Exception, match="The request itself succeeded"):
        sync_client.customers.retrieve("cus_1")


@respx.mock
def test_an_empty_success_body_is_an_empty_object(sync_client: BillKit) -> None:
    respx.get("https://test.billkit.eu/v1/customers/cus_1").mock(
        return_value=httpx.Response(200, content=b"")
    )
    customer = sync_client.customers.retrieve("cus_1")
    assert len(customer) == 0
    assert customer.id is None


# ── pickling ───────────────────────────────────────────────────────


def _page_of_events() -> ListObject[Event]:
    return construct(
        ListObject[Event],
        {
            "object": "list",
            "data": [
                {
                    "id": "evt_1",
                    "object": "event",
                    "type": "subscription.updated",
                    "created": 1,
                    "livemode": False,
                    "data": SUB,
                    "previous_attributes": {"status": "trialing"},
                },
                {
                    "id": "evt_2",
                    "object": "event",
                    "type": "crypto.mined",
                    "created": 2,
                    "livemode": True,
                    "data": {"id": "x", "hash": "abc", "nested": {"k": [1, 2]}},
                    "previous_attributes": None,
                    "brand_new_top_level": "kept",
                },
            ],
            "has_more": True,
        },
    )


def test_a_page_pickles() -> None:
    """``ListObject[Event]`` is created on demand, so pickle could not
    import it by name; the page is rebuilt from ``ListObject`` and ``Event``."""
    page = _page_of_events()
    for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
        back = pickle.loads(pickle.dumps(page, protocol=protocol))
        assert type(back) is type(page)
        assert back == page
        assert back.to_dict() == page.to_dict()
        assert type(back.data[0]) is SubscriptionUpdatedEvent
        assert type(back.data[0].data) is Subscription
        assert back.has_more is True


def test_an_unparametrised_page_pickles() -> None:
    page = construct(ListObject, {"object": "list", "data": [{"a": 1}], "has_more": False})
    back = pickle.loads(pickle.dumps(page))
    assert type(back) is ListObject
    assert back.to_dict() == page.to_dict()


def test_single_objects_and_events_pickle() -> None:
    known, unknown = _page_of_events().data
    for obj in (known, unknown, sub(), known.data, unknown.data):
        back = pickle.loads(pickle.dumps(obj))
        assert type(back) is type(obj)
        assert back.to_dict() == obj.to_dict()
        assert set(back) == set(obj)


# ── Pydantic serialisation is the wire ─────────────────────────────


def test_model_dump_is_to_dict_for_events_pages_and_nested_objects() -> None:
    page = _page_of_events()
    expected = page.to_dict()
    # The polymorphic parts survive: a typed event's data, an unknown
    # event's data, and the unknown top-level key.
    assert expected["data"][0]["data"]["id"] == "sub_1"
    assert expected["data"][1]["data"]["nested"] == {"k": [1, 2]}
    assert expected["data"][1]["brand_new_top_level"] == "kept"
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # unvalidated values used to warn
        assert page.model_dump() == expected
        assert page.model_dump(mode="json") == expected
        assert json.loads(page.model_dump_json()) == expected
        for obj in (page.data[0], page.data[1], page.data[0].data, page.data[1].data):
            assert obj.model_dump() == obj.to_dict()
            assert json.loads(obj.model_dump_json()) == obj.to_dict()


def test_model_dump_emits_no_null_for_a_field_never_sent() -> None:
    s = construct(Subscription, {"id": "sub_1", "status": "active", "customer": {"id": "c"}})
    assert s.model_dump() == {"id": "sub_1", "status": "active", "customer": {"id": "c"}}


def test_a_billkit_object_inside_your_own_model_dumps_as_the_wire() -> None:
    from pydantic import BaseModel, ConfigDict

    class Envelope(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        anything: BillKitObject
        event: Event
        page: ListObject[Event]

    page = _page_of_events()
    env = Envelope(anything=page.data[0].data, event=page.data[0], page=page)
    assert env.model_dump() == {
        "anything": page.data[0].data.to_dict(),
        "event": page.data[0].to_dict(),
        "page": page.to_dict(),
    }


def test_fastapi_jsonable_encoder_is_to_dict() -> None:
    encoders = pytest.importorskip("fastapi.encoders")
    page = _page_of_events()
    assert encoders.jsonable_encoder(page) == page.to_dict()
    assert encoders.jsonable_encoder(page.data[1]) == page.data[1].to_dict()


# ── construct and field names ──────────────────────────────────────


def test_construct_treats_every_key_as_data() -> None:
    """``model_construct(_fields_set=..., **data)`` raised on a wire key
    named ``_fields_set``; any key at all is just data."""
    s = construct(Subscription, {"id": "sub_1", "_fields_set": ["x"], "_private": 1})
    assert s["_fields_set"] == ["x"]
    assert s.to_dict() == {"id": "sub_1", "_fields_set": ["x"], "_private": 1}
    assert s.model_fields_set == {"id"}


_SYNTHETIC_SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "info": {"title": "t", "version": "1.0.0"},
    "paths": {
        "/v1/widgets": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WidgetResponse"}
                            }
                        }
                    }
                }
            }
        },
        "/v1/events": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/EventResponse"}
                            }
                        }
                    }
                }
            }
        },
    },
    "components": {
        "schemas": {
            "WidgetResponse": {
                "type": "object",
                "required": ["id", "items", "keys"],
                "properties": {
                    "id": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                    "keys": {"type": "integer"},
                    "values": {"type": "string"},
                    "get": {"type": "string"},
                    "to_dict": {"type": "string"},
                    "copy": {"type": "string"},
                    "dict": {"type": "string"},
                    "json": {"type": "string"},
                    "model_kind": {"type": "string"},
                    "from": {"type": "string"},
                    "type": {"type": "string"},
                },
            },
            "EventResponse": {
                "type": "object",
                "required": ["id", "data", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "data": {"type": "object"},
                    "type": {"type": "string"},
                },
            },
            "ErrorDetail": {"type": "object", "properties": {"message": {"type": "string"}}},
        }
    },
}


def _generator() -> Any:
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import generate_models

    return generate_models


def test_reserved_names_cover_every_public_attribute_of_a_model() -> None:
    reserved = _generator().RESERVED_ATTRIBUTES
    public = {name for name in dir(BillKitObject) if not name.startswith("_")}
    assert public <= reserved


def test_a_spec_field_that_would_shadow_a_method_gets_a_safe_attribute() -> None:
    source = _generator().Generator(_SYNTHETIC_SPEC).generate()
    namespace: dict[str, Any] = {}
    exec(compile(source, "<generated>", "exec"), namespace)
    widget_cls = namespace["Widget"]
    wire = {
        "id": "w_1",
        "items": ["a", "b"],
        "keys": 3,
        "values": "v",
        "get": "g",
        "to_dict": "t",
        "copy": "c",
        "dict": "d",
        "json": "j",
        "model_kind": "m",
        "from": "f",
        "type": "widget",
    }
    w = construct(widget_cls, wire)
    # The methods still work...
    assert set(w.keys()) == set(wire)
    assert w.get("items") == ["a", "b"]
    assert w.to_dict() == wire
    assert w.model_dump() == wire
    # ...and every field is readable, by its safe attribute and by wire name.
    assert w.items_ == ["a", "b"]
    assert w.keys_ == 3
    assert w.json_ == "j"
    assert w.model_kind_ == "m"
    assert w.from_ == "f"
    assert w.type == "widget"  # a soft keyword is an ordinary name
    for key, value in wire.items():
        assert w[key] == value
    # An absent renamed field is absent as a key, None as an attribute.
    partial = construct(widget_cls, {"id": "w_2"})
    assert "items" not in partial
    assert partial.values_ is None
