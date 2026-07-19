"""kasio-notion plugin — register all 10 KASIO tools into Hermes.

Pattern reference: plugins/spotify/__init__.py.
"""

from __future__ import annotations
from .tools import ALL_TOOLS, _check_kasio_available


def register(ctx) -> None:
    """Register 10 kasio tools. Called once by Hermes plugin loader."""
    for name, schema, handler, emoji in ALL_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="kasio",
            schema=schema,
            handler=handler,
            check_fn=_check_kasio_available,
            emoji=emoji,
        )
