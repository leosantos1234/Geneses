import json
import asyncio
from types import SimpleNamespace

from genesis_skills.mcp_adapter import MCPToolResult
from genesis_skills.skills.base import BaseSkill


def test_stdio_json_text_payload_is_decoded_for_skills():
    class Client:
        async def call(self, tool, arguments):
            return MCPToolResult(
                success=True,
                tool_name=tool,
                data={"text": json.dumps({"count": 3, "predictions": [1, 2, 3]})},
            )

    class ConcreteSkill(BaseSkill):
        async def execute(self, inputs):
            return None

    skill = object.__new__(ConcreteSkill)
    skill.manifest = SimpleNamespace(
        allowed_mcp_tools=["get_lstm_predictions"], max_tool_calls=3, skill_id="test"
    )
    skill.mcp_client = Client()
    skill.tool_call_count = 0
    skill._audit_record = None

    result = asyncio.run(skill.call_mcp_tool("get_lstm_predictions", {}))
    assert result == {"count": 3, "predictions": [1, 2, 3]}
