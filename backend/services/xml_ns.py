"""Thread-safe namespace handling for ElementTree serialization.

ElementTree keeps a single *process-global* namespace→prefix map
(``xml.etree.ElementTree._namespace_map``) that ``register_namespace`` mutates.
Worse, ``register_namespace`` deletes any existing entry whose prefix equals the
new one — so two modules that both want the default ('') prefix collide:

* OPDS (``services/opds.py``) wants ''  → Atom namespace
* the download embedder (``services/metadata_embed.py``) wants '' → OPF namespace

Whichever module is imported *last* steals '' for its own namespace and silently
breaks the other. In practice the OPDS feed ended up serialized with ``ns0:``
prefixes (``<ns0:feed xmlns:ns0="http://www.w3.org/2005/Atom">``) which KOReader
and other strict OPDS clients can't parse, so the catalog showed up empty
(GH #15).

This module provides a lock-guarded context manager that re-asserts the caller's
own registrations immediately before it serializes, so each serialization gets
the prefixes it asked for regardless of import order or concurrent callers
(FastAPI runs sync handlers on a thread pool).
"""
from __future__ import annotations

import threading
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from typing import Iterable, Tuple

_LOCK = threading.RLock()


@contextmanager
def namespaces(registrations: Iterable[Tuple[str, str]]):
    """Re-assert ``(prefix, uri)`` registrations, then yield, under a lock.

    Serialize inside the ``with`` block so the global namespace map can't be
    clobbered by another thread between registration and serialization.
    """
    with _LOCK:
        for prefix, uri in registrations:
            ET.register_namespace(prefix, uri)
        yield
