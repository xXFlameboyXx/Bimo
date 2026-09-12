#!/usr/bin/env python3
"""Live OmniRoute LLM provider verification tool.

Sends a sample query to the configured OmniRoute endpoint and verifies
the structured response. Reports connection errors cleanly without crashing.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.agent.context import ConversationContext
from bimo.agent.omniroute import OmniRouteLLM
from bimo.agent.prompts import DEFAULT_BIMO_SYSTEM_PROMPT
from bimo.core.config import Config
from bimo.interfaces.llm import LLMRequest, Message, Role
from bimo.tools.registry import ToolRegistry
from bimo.tools.robot_tools import RobotGetStatusTool, RobotSetFaceTool, RobotSpeakTool


def run_live_llm_check(
    prompt: str = "Hello Bimo, tell me what robot you are.",
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float | None = None,
    with_tools: bool = False,
) -> bool:
    print("\n" + "=" * 68)
    print("      BIMO PHASE 5/6: OMNIROUTE LLM LIVE VERIFICATION")
    print("=" * 68)

    cfg = Config.from_env()
    selected_model = model or cfg.llm.model
    selected_base_url = base_url or cfg.llm.base_url
    selected_api_key = api_key if api_key is not None else cfg.llm.api_key
    selected_timeout = timeout if timeout is not None else cfg.llm.timeout_seconds

    provider = OmniRouteLLM(
        model_name=selected_model,
        api_key=selected_api_key,
        base_url=selected_base_url,
        timeout_seconds=selected_timeout,
    )

    print(f"  Provider           : {provider.provider_name}")
    print(f"  Target Endpoint    : {provider.endpoint_url}")
    print(f"  Configured Model   : {provider.model_name}")
    print(f"  Timeout Seconds    : {selected_timeout}s")
    print(f"  Test Prompt        : \"{prompt}\"")
    print("=" * 68)

    print("\n[1/2] Checking connectivity to OmniRoute gateway...")
    connected = provider.validate_connection()
    if not connected:
        print(f"  [Notice] Health check did not return HTTP 200 at {provider.base_url}")
        print("  (Attempting chat completion request anyway in case root endpoint has no GET handler)...")
    else:
        print("  Gateway connection verified.")

    print(f"\n[2/2] Sending chat completion request ({selected_model})...")
    context = ConversationContext(system_prompt=DEFAULT_BIMO_SYSTEM_PROMPT)
    context.add_user_message(prompt)
    messages = context.get_messages()

    tool_registry: ToolRegistry | None = None
    tools_schema: list[dict[str, Any]] | None = None
    if with_tools:
        tool_registry = ToolRegistry()
        tool_registry.register(RobotSpeakTool())
        tool_registry.register(RobotSetFaceTool())
        tool_registry.register(RobotGetStatusTool())
        tools_schema = tool_registry.get_schemas()
        print(f"  Available Tools    : {', '.join(tool_registry.list_names())}")

    req = LLMRequest(messages=messages, tools=tools_schema, temperature=0.7)

    start_time = time.perf_counter()
    response = provider.generate(req)
    elapsed_sec = time.perf_counter() - start_time

    print("\n" + "=" * 68)
    print("                     INFERENCE RESULTS")
    print("=" * 68)
    print(f"  Elapsed Time       : {elapsed_sec:.3f} seconds")
    print(f"  Provider           : {provider.provider_name}")
    print(f"  Model              : {response.model or selected_model}")
    print(f"  Finish Reason      : {response.finish_reason}")

    if response.usage:
        print(
            f"  Token Usage        : Prompt={response.usage.prompt_tokens}, "
            f"Completion={response.usage.completion_tokens}, Total={response.usage.total_tokens}"
        )

    if response.is_error:
        print(f"\n  [ERROR OCCURRED]   : {response.error}")
        print("\n  [DIAGNOSTIC GUIDANCE]:")
        print(f"  Ensure OmniRoute is running locally: omniroute serve (or docker run on {provider.base_url})")
        print("  Ensure the model name matches an enabled provider in your OmniRoute config.")
        print("=" * 68 + "\n")
        return False

    if response.content:
        print(f"\n  Model Response:\n  \"{response.content}\"")

    if response.has_tool_calls:
        print(f"\n  Tool Calls Requested: {len(response.tool_calls)}")
        for tc in response.tool_calls:
            print(f"    - {tc.name}({tc.arguments})")
            if tool_registry:
                res = tool_registry.execute(tc.name, **tc.arguments)
                print(f"      -> Execution result: success={res.success}, output={res.output}")

    print("\n  Result             : SUCCESS (OmniRoute inference verified)")
    print("=" * 68 + "\n")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Phase 5/6 OmniRoute LLM Live Verification")
    parser.add_argument(
        "--prompt",
        default="Hello Bimo! Who are you?",
        help="Prompt text to test",
    )
    parser.add_argument("--model", default=None, help="LLM model override (e.g., gemini-3.8-flash)")
    parser.add_argument("--base-url", default=None, help="OmniRoute base URL (default from env)")
    parser.add_argument("--api-key", default=None, help="API key override")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout seconds (default from env)")
    parser.add_argument("--with-tools", action="store_true", help="Send Phase 6 safe robot tool schemas")

    args = parser.parse_args()

    success = run_live_llm_check(
        prompt=args.prompt,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        with_tools=args.with_tools,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
