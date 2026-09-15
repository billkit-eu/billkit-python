"""Opt-in logging for the BillKit SDK.

A library must never decide where its host application's logs go, so this
module does exactly what the Python logging HOWTO prescribes for a
library and nothing more: it creates one named logger, attaches a
:class:`~logging.NullHandler` to it, and never touches configuration
again. No :func:`logging.basicConfig`, no level set on the root logger,
no handler on anything we do not own.

The SDK calls ``logger.debug(...)`` unconditionally. With no handler
configured by the application the ``NullHandler`` swallows those records,
so nothing is printed and no "No handlers could be found" warning is
emitted either. Turning it on is the *application's* call::

    import logging

    logging.basicConfig()
    logging.getLogger("billkit").setLevel(logging.DEBUG)

What gets logged
----------------

- **DEBUG**: one line per HTTP attempt and one per response, giving method,
  URL, attempt number, status, elapsed ms, and ``X-Request-Id`` (quote
  that id to BillKit support).
- **WARNING**: one line per retry, naming the reason and the delay
  before the next attempt. A retry is a real anomaly worth surfacing
  without being an error.

What is deliberately never logged
---------------------------------

- The ``Authorization`` header or the API key, in any form.
- Request and response **bodies**. They carry customer PII (emails,
  names, addresses) and billing detail. A payments SDK that quietly
  copies those into its user's log files has manufactured a compliance
  problem on their behalf.
- The **query string**. List filters routinely carry values like
  ``email=ada@example.com``. Only the path is logged.
- The **final failure**. Every exhausted call raises a typed
  :class:`~billkit.BillKitError` carrying the status, request id and
  retry-after; logging it here as well would produce a duplicate the
  caller never asked for and cannot suppress from their own handler.
"""

from __future__ import annotations

import logging

#: The SDK's one logger. Configure it from your application: set a
#: level, add a handler, or attach it to your existing config. The SDK
#: only ever writes to it.
logger = logging.getLogger("billkit")

# The library-author contract: give the logger somewhere to go so records
# are discarded silently until the application opts in, and never add a
# real handler ourselves.
logger.addHandler(logging.NullHandler())

#: Loggers that undo this module's query-free promise from the outside.
_LEAKY_LOGGERS = ("httpx", "httpcore")


def sdk_logging_configured() -> bool:
    """True once the application has opted this SDK's logger in.

    "Configured" means the app set a level on ``billkit`` or attached a
    real (non-null) handler to it — the two things the HOWTO snippet
    above does. An app that has done neither is still in the default
    silent state, and nothing below should fire.
    """
    if logger.level != logging.NOTSET:
        return True
    return any(not isinstance(h, logging.NullHandler) for h in logger.handlers)


def quiet_leaky_request_logs() -> tuple[str, ...]:
    """Stop ``httpx`` echoing the full request URL, query string included.

    The promise this module makes — "only the path is logged" — is one
    the SDK can keep for its *own* records and cannot keep for anyone
    else's. ``httpx`` logs every request at INFO as
    ``HTTP Request: GET https://api.billkit.eu/v1/customers?email=ada@
    example.com "HTTP/1.1 200 OK"``, using ``httpx._client``'s own
    module logger. There is no per-client switch for it, no event hook
    that suppresses it, and nothing about turning BillKit's logging on
    that should also turn a customer's email address into a log line.

    So: when the app opts this SDK in, and *only* for loggers it has not
    configured itself, raise ``httpx``/``httpcore`` to WARNING. The
    ``NOTSET`` check is what keeps this from stomping global config — an
    app that has deliberately set ``httpx`` to INFO has made a decision,
    and a billing SDK does not get to overrule it (see the README's
    logging caveat).

    Returns the logger names actually changed.
    """
    if not sdk_logging_configured():
        return ()
    changed: list[str] = []
    for name in _LEAKY_LOGGERS:
        leaky = logging.getLogger(name)
        if leaky.level == logging.NOTSET:
            leaky.setLevel(logging.WARNING)
            changed.append(name)
    return tuple(changed)


__all__ = ["logger", "quiet_leaky_request_logs", "sdk_logging_configured"]
