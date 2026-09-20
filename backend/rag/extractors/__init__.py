"""Per-format extraction helpers behind :mod:`backend.rag.extract`.

``extract.py`` stays the public façade — ``Block``, ``extract_blocks`` and the
suffix dispatch table — because five modules import it by that path. The
format-specific bodies live here so each stays readable on its own.

The module names carry a ``_shapes`` / ``_body`` suffix rather than being
called ``pptx.py`` and ``docx.py``: both modules import the third-party
``pptx`` / ``docx`` packages *inside themselves*, and a same-named sibling is
a readability trap with no upside.
"""

from backend.rag.extractors.omml import M_NS, omml_to_latex

__all__ = ["M_NS", "omml_to_latex"]
