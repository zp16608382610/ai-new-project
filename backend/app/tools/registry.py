"""Tool Registry (Phase 4B).

The registry is an explicit allowlist - the only way a tool name becomes
callable. The Agent / Executor must never resolve tools through getattr(),
eval() or dynamic importlib: unknown names simply miss the registry and the
executor reports UNKNOWN_TOOL.
"""
from __future__ import annotations

from app.tools.base import ToolDefinition
from app.tools.errors import ToolRegistryError


class ToolRegistry:
    """Name -> ToolDefinition allowlist with duplicate-protection."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Register one tool; duplicate registration raises explicitly."""
        if not isinstance(tool, ToolDefinition):
            raise ToolRegistryError(
                f"Only ToolDefinition instances can be registered, got {type(tool).__name__}"
            )
        if not tool.name or not tool.name.strip():
            raise ToolRegistryError("Tool name must be a non-empty string")
        if tool.name in self._tools:
            raise ToolRegistryError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Return the registered definition or None (no dynamic lookup)."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(self) -> list[ToolDefinition]:
        """All registered tools sorted by name (deterministic)."""
        return sorted(self._tools.values(), key=lambda tool: tool.name)

    def __len__(self) -> int:
        return len(self._tools)