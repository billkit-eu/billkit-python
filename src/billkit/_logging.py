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

__all__ = ["logger"]
