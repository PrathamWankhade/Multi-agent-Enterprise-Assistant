"""Enterprise tool framework: registry + mock HR/IT/business tools."""

from app.tools.registry import ToolSpec, get_registry
from app.tools import enterprise  # noqa: F401  (registers built-in tools)

__all__ = ["ToolSpec", "get_registry"]
