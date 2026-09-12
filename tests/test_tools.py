"""Unit tests for tools and Tool Registry."""

import unittest
from typing import Any

from bimo.interfaces.tools import (
    BaseTool,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


class LightControlTool(BaseTool):
    """Mock lighting tool."""

    def __init__(self) -> None:
        super().__init__(
            name="set_light",
            description="Controls an RGB light or desk strip.",
            parameters=[
                ToolParameter(
                    name="color",
                    type="string",
                    description="Hex color code",
                    required=True,
                ),
                ToolParameter(
                    name="brightness",
                    type="integer",
                    description="Brightness from 0 to 100",
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs: Any) -> ToolResult:
        color = kwargs.get("color")
        if not color:
            return ToolResult(success=False, error="Color parameter missing")
        return ToolResult(success=True, output=f"Light set to {color}")


class FailingTool(BaseTool):
    """Tool that raises an exception for robustness testing."""

    def __init__(self) -> None:
        super().__init__(name="failing_tool", description="Always raises error")

    def execute(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError("Hardware communication error")


class TestTools(unittest.TestCase):
    """Test suite for BaseTool and ToolRegistry."""

    def setUp(self) -> None:
        self.registry = ToolRegistry()

    def test_tool_schema_generation(self) -> None:
        tool = LightControlTool()
        schema = tool.get_schema()

        self.assertEqual(schema["type"], "function")
        func = schema["function"]
        self.assertEqual(func["name"], "set_light")
        self.assertIn("properties", func["parameters"])
        self.assertIn("color", func["parameters"]["properties"])
        self.assertIn("color", func["parameters"]["required"])

    def test_registry_lifecycle(self) -> None:
        tool = LightControlTool()
        self.registry.register(tool)

        self.assertEqual(self.registry.get("set_light"), tool)
        self.assertEqual(len(self.registry.list_tools()), 1)

        schemas = self.registry.get_schemas()
        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "set_light")

        # Execute
        res = self.registry.execute("set_light", color="#00E5FF")
        self.assertTrue(res.success)
        self.assertEqual(res.output, "Light set to #00E5FF")

        # Unregister
        unregistered = self.registry.unregister("set_light")
        self.assertTrue(unregistered)
        self.assertIsNone(self.registry.get("set_light"))

    def test_execute_nonexistent_tool(self) -> None:
        import logging

        logging.disable(logging.CRITICAL)
        try:
            res = self.registry.execute("non_existent_tool")
        finally:
            logging.disable(logging.NOTSET)

        self.assertFalse(res.success)
        self.assertIn("not registered", str(res.error))

    def test_execute_failing_tool_gracefully_catches_error(self) -> None:
        import logging

        self.registry.register(FailingTool())
        logging.disable(logging.CRITICAL)
        try:
            res = self.registry.execute("failing_tool")
        finally:
            logging.disable(logging.NOTSET)

        self.assertFalse(res.success)
        self.assertIn("Hardware communication error", str(res.error))


if __name__ == "__main__":
    unittest.main()
