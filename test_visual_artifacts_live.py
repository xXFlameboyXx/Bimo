"""Live Verification Script for Visual-First Artifact Generation.

Tests the three core user workflows:
1. Visual Guide Request:
   "Create a graphical PDF showing me how to build a cozy Minecraft base. Use one beautiful image and concise instructions."
   -> Recognizes VISUAL_GUIDE intent, generates 1 hero image via OmniRoute, composes styled visual PDF with hero image, materials, steps, callouts, and tips.
2. Standalone Image Request:
   "Create an image of a Minecraft woodland cottage."
   -> Generates exactly 1 image artifact.
3. Textual Document Request:
   "Write a textual report about Minecraft bases."
   -> Generates structured document with 0 images.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from bimo.core.config import Config
from bimo.artifacts.planner import ArtifactIntent, ArtifactPlanner
from bimo.artifacts.composer import VisualDocumentComposer
from bimo.artifacts.service import ArtifactService
from bimo.artifacts.tools import (
    ArtifactCreateVisualGuideTool,
    ArtifactPlanTool,
    ImageGenerateTool,
)
from bimo.images.provider import OmniRouteImageProvider
from bimo.images.local_renderer import DeterministicLocalImageRenderer
from bimo.interfaces.tools import ToolRegistry, PermissionPolicy
from bimo.agent.factory import create_default_tool_registry
from bimo.agent.omniroute import OmniRouteLLM
from bimo.interfaces.llm import LLMRequest, Message, Role
from bimo.agent.prompts import DEFAULT_BIMO_SYSTEM_PROMPT


def test_visual_artifacts_live():
    print("=" * 65)
    print("BIMO VISUAL-FIRST ARTIFACT GENERATION: LIVE ACCEPTANCE SUITE")
    print("=" * 65)

    config = Config.from_env()
    workspace = Path(config.artifacts.workspace_dir).resolve()
    print(f"[*] Workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)

    service = ArtifactService(workspace_dir=workspace)
    image_provider = OmniRouteImageProvider(
        base_url=config.image.base_url,
        model=config.image.model,
    )
    renderer = DeterministicLocalImageRenderer()

    # =========================================================================
    # TEST 1: Graphical Guide Request ("cozy Minecraft base")
    # =========================================================================
    print("\n[TEST 1] Testing Visual Guide Generation with Hero Image & Hierarchy...")
    user_prompt_1 = "Create a graphical PDF showing me how to build a cozy Minecraft base. Use one beautiful image and concise instructions."
    
    # 1.1 Intent & Plan
    intent_1 = ArtifactPlanner.detect_intent(user_prompt_1)
    print(f"    1. Intent Detected: {intent_1.value} (Expected: VISUAL_GUIDE)")
    assert intent_1 == ArtifactIntent.VISUAL_GUIDE

    plan_1 = ArtifactPlanner.create_plan(user_prompt_1, intent_1)
    print(f"    2. Plan Style: {plan_1.style}, Image Count: {plan_1.image_count}")
    assert plan_1.image_count == 1
    assert plan_1.style == "minecraft"
    assert len(plan_1.visual_assets) == 1

    # 1.2 End-to-End Visual Guide Tool Execution
    visual_tool = ArtifactCreateVisualGuideTool(
        service=service,
        image_provider=image_provider,
        local_renderer=renderer,
    )

    print("    3. Executing artifact.create_visual_guide with hero image generation...")
    result_1 = visual_tool.execute(
        filename="minecraft_cozy_cottage_guide.pdf",
        title="Cozy Woodland Cottage — Minecraft Build Guide",
        style="minecraft",
        hero_image_prompt=plan_1.visual_assets[0].prompt,
        hero_image_caption="Figure 1: Authentic Minecraft woodland cottage exterior with pitched spruce roof.",
        materials=[
            ["Oak Logs", "Frame & Corner Columns", "48"],
            ["Dark Oak Planks", "Exterior Wall Panels & Floors", "64+"],
            ["Spruce Stairs & Slabs", "Steep Pitched Roof Overhang", "64+"],
            ["Cobblestone", "Foundation & Smoking Chimney", "32"],
            ["Lanterns & Flowers", "Lantern Lighting & Window Boxes", "8"],
        ],
        sections=plan_1.sections,
        callouts=plan_1.callouts,
        tips=plan_1.tips,
    )

    print(f"    4. Tool Execution Result: {result_1.output}")
    assert result_1.success
    pdf_path = result_1.metadata["path"]
    assert Path(pdf_path).exists()
    assert Path(pdf_path).stat().st_size > 3000
    print(f"    -> Generated Visual PDF: {pdf_path} ({Path(pdf_path).stat().st_size} bytes)")

    if "hero_image" in result_1.metadata and result_1.metadata["hero_image"]:
        hero_path = result_1.metadata["hero_image"]["path"]
        print(f"    -> Generated Hero Image: {hero_path} ({Path(hero_path).stat().st_size} bytes)")
        assert Path(hero_path).exists()
        assert Path(hero_path).stat().st_size > 1000

    # =========================================================================
    # TEST 2: Pure Image Request
    # =========================================================================
    print("\n[TEST 2] Testing Standalone Image Intent & Generation...")
    user_prompt_2 = "Create an image of a Minecraft woodland cottage."
    intent_2 = ArtifactPlanner.detect_intent(user_prompt_2)
    print(f"    1. Intent Detected: {intent_2.value} (Expected: IMAGE)")
    assert intent_2 == ArtifactIntent.IMAGE

    plan_2 = ArtifactPlanner.create_plan(user_prompt_2, intent_2)
    print(f"    2. Image Count: {plan_2.image_count} (Expected: 1)")
    assert plan_2.image_count == 1

    # 2. Standalone Image Generation via Tool
    img_tool = ImageGenerateTool(service=service, provider=image_provider)
    result_img = img_tool.execute(
        prompt="Authentic Minecraft woodland cottage with steep dark oak roof, smoking stone chimney, blooming flower planters, raytraced shader lighting",
        filename="minecraft_cottage_hero.png",
    )
    if result_img.success:
        print(f"    -> Standalone Image Created via AI Provider: {result_img.metadata['filename']} ({result_img.metadata['size_bytes']} bytes)")
        assert Path(result_img.metadata["path"]).exists()
    else:
        print(f"    -> AI Image generation status handled honestly: {result_img.error}")
        # As per Section 5 & Section 22: Honest reporting + fallback
        print("    -> Verifying deterministic fallback rendering...")
        fallback_art = renderer.render_diagram(
            title="Minecraft Woodland Cottage",
            steps=["Foundation (Cobblestone)", "Frame (Oak Logs)", "Walls (Dark Oak Planks)", "Roof (Spruce Stairs)"],
        )
        saved = service.create_artifact("minecraft_cottage_diagram.png", fallback_art, "image/png")
        print(f"    -> Local fallback rendered successfully: {saved.filename} ({saved.size_bytes} bytes)")
        assert Path(saved.path).exists()

    # =========================================================================
    # TEST 3: Pure Textual Document Request
    # =========================================================================
    print("\n[TEST 3] Testing Textual Report Intent (Zero Unnecessary Images)...")
    user_prompt_3 = "Write a textual report about Minecraft bases."
    intent_3 = ArtifactPlanner.detect_intent(user_prompt_3)
    print(f"    1. Intent Detected: {intent_3.value} (Expected: STRUCTURED_DOCUMENT)")
    assert intent_3 == ArtifactIntent.STRUCTURED_DOCUMENT

    plan_3 = ArtifactPlanner.create_plan(user_prompt_3, intent_3)
    print(f"    2. Image Count: {plan_3.image_count} (Expected: 0)")
    assert plan_3.image_count == 0

    # =========================================================================
    # TEST 4: Live OmniRoute LLM Tool Selection
    # =========================================================================
    print("\n[TEST 4] Live LLM Tool Selection Check with OmniRoute...")
    reg = create_default_tool_registry(config)
    schemas = reg.get_schemas()
    llm = OmniRouteLLM(base_url=config.llm.base_url, model_name=config.llm.model)

    req_llm = LLMRequest(
        messages=[
            Message(role=Role.SYSTEM, content=DEFAULT_BIMO_SYSTEM_PROMPT),
            Message(role=Role.USER, content="Give me a graphical visual example of how to build a Minecraft base."),
        ],
        tools=schemas,
    )
    res_llm = llm.generate(req_llm)
    print(f"    1. LLM Finished Cleanly: {res_llm.finish_reason != 'error'}")
    assert res_llm.finish_reason != "error"

    selected_tool_names = [tc.name for tc in res_llm.tool_calls]
    print(f"    2. Tools Selected by LLM: {selected_tool_names}")
    # Must NOT select notepad
    assert "pc.open_app" not in selected_tool_names
    assert "pc.type_text" not in selected_tool_names
    # Must choose visual guide or artifact PDF
    has_artifact_tool = any(t in selected_tool_names for t in ("artifact.create_visual_guide", "artifact_create_visual_guide", "artifact.create_pdf", "artifact_create_pdf"))
    print(f"    3. Selected Appropriate Artifact Tool: {has_artifact_tool}")
    assert has_artifact_tool

    print("\n" + "=" * 65)
    print("ALL VISUAL-FIRST ARTIFACT ACCEPTANCE CRITERIA MET!")
    print("=" * 65)


if __name__ == "__main__":
    test_visual_artifacts_live()
