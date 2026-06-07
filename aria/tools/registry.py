"""
aria/tools/registry.py
────────────────────────
Central registry of all ARIA tools.

The registry:
  1. Holds a mapping from tool name → tool instance.
  2. Provides a method to load tools from the tools/ directory dynamically
     (used after an improvement is deployed).
  3. Is the single source of truth for which tools exist.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

from aria.tools.base import BaseTool


class ToolRegistry:
    """
    Central store of all BaseTool instances known to ARIA.
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Manually register a tool instance."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return a tool by name, or None if not registered."""
        return self._tools.get(name)

    def all_tools(self) -> list[BaseTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def reload_tool(self, tool_name: str) -> bool:
        """
        Hot-reload a specific tool module from the tools/ directory.
        Called after a successful improvement deployment.

        Returns True if reload succeeded, False otherwise.
        """
        module_name = f"aria.tools.{tool_name}"
        try:
            module = importlib.import_module(module_name)
            importlib.reload(module)

            # Find the BaseTool subclass in the reloaded module
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                ):
                    instance = obj()
                    self.register(instance)
                    return True
        except Exception:
            pass
        return False

    def load_all_from_directory(self, tools_dir: Path | None = None) -> None:
        """
        Discover and register all tools in the tools/ directory.
        Called once at startup.
        """
        from aria.tools.calculator_tool import CalculatorTool
        from aria.tools.code_executor_tool import CodeExecutorTool
        from aria.tools.search_tool import SearchTool
        from aria.tools.summarizer_tool import SummarizerTool
        from aria.tools.weather_tool import WeatherTool

        for tool_cls in [
            SearchTool,
            SummarizerTool,
            CalculatorTool,
            CodeExecutorTool,
            WeatherTool,
        ]:
            self.register(tool_cls())


# ── Shared singleton ──────────────────────────────────────────────────────────

registry = ToolRegistry()
