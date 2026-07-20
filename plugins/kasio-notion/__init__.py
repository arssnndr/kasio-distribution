"""kasio-notion plugin — register all 11 KASIO tools into Hermes.

Pattern reference: plugins/spotify/__init__.py.

The 11 tools are:
  - 4 transaction tools (list/save/update/archive)
  - 4 account tools (list/save/update/archive)
  - 3 utility tools (parse_nominal, read_receipt, read_screenshot)
"""

from __future__ import annotations

# Support both package mode (production: Hermes loads as plugin package) and
# top-level mode (testing: pytest may try to load this directly).
try:
    from .tools import ALL_TOOLS, _check_kasio_available
except ImportError:
    from tools import ALL_TOOLS, _check_kasio_available


def register(ctx) -> None:
    """Register 11 kasio tools. Called once by Hermes plugin loader."""
    for name, schema, handler, emoji in ALL_TOOLS:
        ctx.register_tool(
            name=name,
            toolset="kasio",
            schema=schema,
            handler=handler,
            check_fn=_check_kasio_available,
            emoji=emoji,
        )
