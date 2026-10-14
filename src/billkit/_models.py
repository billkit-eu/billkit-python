"""The base classes every response is built from.

:class:`BillKitObject` is a Pydantic v2 model that is *also* a read-only
:class:`~collections.abc.Mapping`. Both access styles are supported
permanently and neither is deprecated:

.. code-block:: python

    sub = client.subscriptions.retrieve("sub_123")
    sub.status            # typed: str
    sub["status"]         # the 0.x style, still works
    sub.get("status")     # likewise
    "discount" in sub     # True when the API sent the key
    sub.to_dict()         # plain JSON types, recursively

**Responses are never validated.** A payments SDK must not turn a
successful charge into an exception because the server added a field,
widened an enum or sent a type this version did not expect. Responses are
built with :func:`construct`, which converts nested objects into their
models and otherwise keeps every value exactly as the API sent it. The
annotations describe the contract; they do not police it.

**The mapping view is the wire.** Keys are the fields the API actually
sent, in the model's declared order, followed by any field this SDK version
does not know. A declared field the API did not send reads as ``None`` as
an attribute and is absent as a key, which is precisely how the 0.x dicts
behaved. An unknown field is readable both ways (``obj.new_field`` and
``obj["new_field"]``) and its value is plain JSON.

**Every way out is the wire.** ``to_dict()``, ``model_dump()``,
``model_dump_json()`` and anything built on them (FastAPI's
``jsonable_encoder``, a response model) produce the same thing: the keys
the API sent, polymorphic values (an event's ``data``, the items of a
page) included, and no ``None`` for a field that was never sent.
Pydantic's ``include``/``exclude``/``by_alias`` options do not apply;
filter the dict instead.

Equality is wire equality: two objects are equal when they are of the same
class and their ``to_dict()`` is equal, so an object the API sent
``"discount": null`` is not equal to one where ``discount`` was absent. An
object never equals a ``dict``; compare ``obj.to_dict()`` when that is
what you mean.

**Field names.** A field is declared under its wire name, except where
that name would shadow a method every model has (``items``, ``keys``,
``values``, ``get``, ``to_dict``, ``copy``, ``dict``, ``json``, any
``model_*``) or is a Python keyword: then the attribute is the wire name
plus ``_`` (``obj.items_``), and ``obj["items"]`` and ``to_dict()`` keep
the wire name.

Pages and objects pickle, so a response can cross a process boundary.
"""

from __future__ import annotations

import functools
import types
import typing
from collections.abc import ItemsView, Iterator, KeysView, Mapping, ValuesView
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, model_serializer

__all__ = ["BillKitObject", "ListObject", "construct"]

_T = TypeVar("_T")
_M = TypeVar("_M", bound="_BillKitModel")


class _BillKitModel(BaseModel):
    """What every response model shares: :class:`BillKitObject` and
    :class:`ListObject`. Not public; type against those two."""

    model_config = ConfigDict(
        extra="allow",
        protected_namespaces=(),
        arbitrary_types_allowed=True,
        # Never validated (see ``construct``), so revalidating on assignment
        # would only surprise.
        validate_assignment=False,
    )

    # -- the wire view ---------------------------------------------------

    def _wire_keys(self) -> Iterator[str]:
        fields_set = self.model_fields_set
        for name, wire in _wire_names(type(self)).items():
            if name in fields_set:
                yield wire
        if self.__pydantic_extra__:
            yield from self.__pydantic_extra__

    def _wire_get(self, key: str) -> Any:
        name = _field_for_wire(type(self)).get(key)
        if name is not None:
            if name in self.model_fields_set:
                return getattr(self, name)
            raise KeyError(key)
        extra = self.__pydantic_extra__
        if extra is not None and key in extra:
            return extra[key]
        raise KeyError(key)

    def _has_wire_key(self, key: str) -> bool:
        try:
            self._wire_get(key)
        except KeyError:
            return False
        return True

    # -- conversion ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """This object as plain JSON types, recursively.

        Exactly the keys the API sent (unknown ones included), with every
        nested object converted too. Use it to store a response, forward it
        or compare it with a literal.
        """
        return {key: _plain(self._wire_get(key)) for key in self._wire_keys()}

    @model_serializer(mode="plain")
    def _serialize(self) -> dict[str, Any]:
        # Pydantic would otherwise serialise by the *declared* types: an
        # event's ``data: BillKitObject`` and the items of a page came out
        # as ``{}``, a field the API never sent came out as ``None``, and
        # every unvalidated value warned. The wire view has none of those
        # problems, so ``model_dump()`` and ``model_dump_json()`` (and
        # FastAPI's ``jsonable_encoder``) are ``to_dict()``.
        return self.to_dict()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _BillKitModel):
            return NotImplemented
        return type(self) is type(other) and self.to_dict() == other.to_dict()

    __hash__ = None  # type: ignore[assignment]


@functools.cache
def _wire_names(cls: type[_BillKitModel]) -> dict[str, str]:
    """Field name -> wire name, in declaration order."""
    return {name: info.alias or name for name, info in cls.model_fields.items()}


@functools.cache
def _field_for_wire(cls: type[_BillKitModel]) -> dict[str, str]:
    """Wire name -> field name."""
    return {wire: name for name, wire in _wire_names(cls).items()}


class BillKitObject(_BillKitModel, Mapping[str, Any]):
    """Base class of every object the API returns, except a list page.

    Attribute access is typed; mapping access (``obj["key"]``,
    ``obj.get("key")``, ``"key" in obj``, ``dict(obj)``, iteration over
    keys) is the 0.x behaviour and stays supported. Mutable like the
    objects of other Python payment SDKs, but the mapping view is
    read-only: there is no ``obj["key"] = value``.
    """

    #: Subclass dispatch for polymorphic objects, keyed by the value of
    #: ``__bk_discriminator__``. Set on :class:`billkit.types.Event` so an
    #: event of a known ``type`` is built as its specific class.
    __bk_variants__: ClassVar[Mapping[str, type[BillKitObject]] | None] = None
    __bk_discriminator__: ClassVar[str] = "type"

    # -- Mapping protocol ------------------------------------------------

    def __getitem__(self, key: str) -> Any:
        return self._wire_get(key)

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return self._wire_keys()

    def __len__(self) -> int:
        return sum(1 for _ in self._wire_keys())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self._has_wire_key(key)

    def keys(self) -> KeysView[str]:
        return self._snapshot().keys()

    def values(self) -> ValuesView[Any]:
        return self._snapshot().values()

    def items(self) -> ItemsView[str, Any]:
        return self._snapshot().items()

    def _snapshot(self) -> dict[str, Any]:
        return {key: self._wire_get(key) for key in self._wire_keys()}


_LIST_KEY_HINT = (
    "A ListObject is indexed by envelope key ('data', 'has_more', 'object'), "
    "not by position; read the items as page.data[i], or iterate the page."
)


class ListObject(_BillKitModel, Generic[_T]):
    """One page of a list endpoint: ``{"object": "list", "data": [...], "has_more": ...}``.

    A page is a sequence of its items, as in stripe-python: iterating it
    yields the items, ``len(page)`` counts them and ``item in page`` looks
    for one. It is **not** a :class:`~collections.abc.Mapping`, because a
    mapping that iterates something other than its keys breaks every
    consumer of the protocol.

    The 0.x envelope reads still work with a string key: ``page["data"]``,
    ``page["has_more"]``, ``page["object"]``, ``page.get("has_more")``, and
    ``"has_more" in page`` tests for the key. Any other key type is a
    ``TypeError``. ``page.to_dict()`` is the whole envelope as plain JSON.
    ``iter()`` on a resource walks every page for you.
    """

    object: str = "list"
    data: list[_T] = []
    has_more: bool = False

    def __iter__(self) -> Iterator[_T]:  # type: ignore[override]
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __bool__(self) -> bool:
        # A page is a value, not a container check: an empty page is still
        # a successful response.
        return True

    def __getitem__(self, key: str) -> Any:
        if not isinstance(key, str):
            raise TypeError(f"{_LIST_KEY_HINT} Got {type(key).__name__} {key!r}.")
        return self._wire_get(key)

    def get(self, key: str, default: Any = None) -> Any:
        """``page[key]``, or ``default`` when the envelope has no such key."""
        if not isinstance(key, str):
            raise TypeError(f"{_LIST_KEY_HINT} Got {type(key).__name__} {key!r}.")
        try:
            return self._wire_get(key)
        except KeyError:
            return default

    def __contains__(self, item: Any) -> bool:
        # A string asks about the envelope (the 0.x ``"data" in page``);
        # anything else asks whether the item is on this page.
        if isinstance(item, str):
            return self._has_wire_key(item)
        return item in self.data

    def __reduce__(self) -> tuple[Any, ...]:
        # ``ListObject[Event]`` is a class Pydantic creates on demand, so
        # pickle cannot import it by name. Rebuild it from the importable
        # generic and its item type.
        meta = type(self).__pydantic_generic_metadata__
        origin = meta["origin"] or type(self)
        return (_restore_page, (origin, meta["args"], self.__getstate__()))


def _restore_page(origin: type[ListObject[Any]], args: tuple[Any, ...], state: Any) -> Any:
    """Unpickle a :class:`ListObject` (see :meth:`ListObject.__reduce__`)."""
    cls = origin[args] if args else origin  # type: ignore[index]
    page = cls.__new__(cls)
    page.__setstate__(state)
    return page


def _plain(value: Any) -> Any:
    if isinstance(value, _BillKitModel):
        return value.to_dict()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def construct(cls: type[_M], data: Mapping[str, Any]) -> _M:
    """Build ``cls`` from decoded JSON without validating it.

    Nested objects become their models wherever the annotation names one;
    every other value is kept exactly as sent. A declared field that is
    absent reads as ``None``; an undeclared one is kept as an extra. Never
    raises on content, which is the point: any key at all, ``_fields_set``
    included, is just data.
    """
    target: type[_BillKitModel] = cls
    # Only the class that owns the table dispatches: building a specific
    # event class directly must not be redirected by the inherited table.
    variants: Mapping[str, type[BillKitObject]] | None = cls.__dict__.get("__bk_variants__")
    if variants is not None:
        discriminator = data.get(getattr(cls, "__bk_discriminator__", "type"))
        if isinstance(discriminator, str):
            target = variants.get(discriminator, target)
    wire_names = _wire_names(target)
    values: dict[str, Any] = {}
    present: set[str] = set()
    for name, info in target.model_fields.items():
        wire = wire_names[name]
        if wire in data:
            values[name] = _convert(info.annotation, data[wire])
            present.add(name)
        elif info.is_required():
            values[name] = None
        else:
            values[name] = info.get_default(call_default_factory=True)
    known = _field_for_wire(target)
    extras = {k: v for k, v in data.items() if isinstance(k, str) and k not in known}
    # Assembled directly rather than through ``model_construct(**data)``,
    # whose own ``_fields_set`` keyword collided with a wire key of that name.
    obj = target.__new__(target)
    object.__setattr__(obj, "__dict__", values)
    object.__setattr__(obj, "__pydantic_fields_set__", present)
    object.__setattr__(obj, "__pydantic_extra__", extras)
    object.__setattr__(obj, "__pydantic_private__", None)
    return obj  # type: ignore[return-value]


def _convert(annotation: Any, value: Any) -> Any:
    if value is None or annotation is None or annotation is Any:
        return value
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            converted = _convert(arg, value)
            if converted is not value:
                return converted
        return value
    if origin is typing.Literal:
        return value
    if origin is list:
        if isinstance(value, list):
            (item,) = typing.get_args(annotation) or (Any,)
            return [_convert(item, v) for v in value]
        return value
    if origin is dict:
        if isinstance(value, dict):
            args = typing.get_args(annotation)
            item = args[1] if len(args) == 2 else Any
            if _is_model(item):
                return {k: _convert(item, v) for k, v in value.items()}
        return value
    if _is_model(annotation):
        if isinstance(value, Mapping):
            return construct(annotation, value)
        return value
    return value


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, _BillKitModel)
