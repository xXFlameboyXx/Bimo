"""Unit tests for PhysicalFaceRenderer and display factory.

Validates that PhysicalFaceRenderer:
1. Conforms to BaseFaceRenderer contract.
2. Pre-caches and scales all 800x480 sprites down to 480x320 RGB565 byte buffers.
3. Supports all 8 robot states and IDLE sub-categories.
4. Performs mock framebuffer operations (clear, rect, text, frame rendering).
5. Integrates with create_face_renderer factory.
"""

from pathlib import Path
import sys
import unittest

# Ensure src/ is on path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from bimo.core.config import Config, DisplayConfig
from bimo.core.state import RobotState
from bimo.rendering import (
    BaseFaceRenderer,
    FaceSimulator,
    IdleSubCategoryConfig,
    PhysicalFaceRenderer,
    create_face_renderer,
)
from bimo.rendering.physical_lcd import rgb_to_rgb565


class TestPhysicalFaceRenderer(unittest.TestCase):
    """Test suite for PhysicalFaceRenderer running in mock framebuffer mode."""

    def setUp(self) -> None:
        self.faces_dir = Path(__file__).resolve().parent.parent / "faces"
        self.renderer = PhysicalFaceRenderer(
            width=480,
            height=320,
            faces_dir=self.faces_dir,
            mock=True,
            clear_on_close=True,
        )
        self.renderer.initialize()

    def tearDown(self) -> None:
        self.renderer.close()

    def test_implements_base_face_renderer(self) -> None:
        self.assertIsInstance(self.renderer, BaseFaceRenderer)
        self.assertEqual(self.renderer.width, 480)
        self.assertEqual(self.renderer.height, 320)
        self.assertTrue(self.renderer.is_running)
        self.assertTrue(self.renderer.is_mock)
        self.assertEqual(self.renderer.buffer_size, 480 * 320 * 2)

    def test_rgb_to_rgb565_conversion(self) -> None:
        # Pure Red (255, 0, 0) -> 0xF800
        self.assertEqual(rgb_to_rgb565(255, 0, 0), 0xF800)
        # Pure Green (0, 255, 0) -> 0x07E0
        self.assertEqual(rgb_to_rgb565(0, 255, 0), 0x07E0)
        # Pure Blue (0, 0, 255) -> 0x001F
        self.assertEqual(rgb_to_rgb565(0, 0, 255), 0x001F)
        # Pure Black (0, 0, 0) -> 0x0000
        self.assertEqual(rgb_to_rgb565(0, 0, 0), 0x0000)
        # Pure White (255, 255, 255) -> 0xFFFF
        self.assertEqual(rgb_to_rgb565(255, 255, 255), 0xFFFF)

    def test_clear_colors(self) -> None:
        # Clear Red (little endian 0xF800 -> low 0x00, high 0xF8)
        self.renderer.clear((255, 0, 0))
        buf = self.renderer.get_buffer()
        self.assertEqual(len(buf), self.renderer.buffer_size)
        self.assertEqual(buf[0], 0x00)
        self.assertEqual(buf[1], 0xF8)

        # Clear Green (0x07E0 -> low 0xE0, high 0x07)
        self.renderer.clear((0, 255, 0))
        buf = self.renderer.get_buffer()
        self.assertEqual(buf[0], 0xE0)
        self.assertEqual(buf[1], 0x07)

        # Clear Blue (0x001F -> low 0x1F, high 0x00)
        self.renderer.clear((0, 0, 255))
        buf = self.renderer.get_buffer()
        self.assertEqual(buf[0], 0x1F)
        self.assertEqual(buf[1], 0x00)

        # Clear Black
        self.renderer.clear(0)
        buf = self.renderer.get_buffer()
        self.assertEqual(buf, bytes(self.renderer.buffer_size))

    def test_draw_rect_and_text(self) -> None:
        self.renderer.clear(0)
        # Draw small green box at (10, 10) of size (20, 20)
        self.renderer.draw_rect(10, 10, 20, 20, color=(0, 255, 0), fill=True)
        buf = self.renderer.get_buffer()
        # Verify it's no longer pure black
        self.assertNotEqual(buf, bytes(self.renderer.buffer_size))

        # Test text rendering
        self.renderer.draw_text("TEST", 50, 50, color=(255, 255, 255))
        buf2 = self.renderer.get_buffer()
        self.assertNotEqual(buf2, bytes(self.renderer.buffer_size))

    def test_sprite_caching_and_rendering_all_states(self) -> None:
        # Check that IDLE subcategories loaded
        self.assertGreaterEqual(self.renderer.idle_categories_count, 1)

        # Check rendering for each state in RobotState
        for state in RobotState:
            self.renderer.set_state(state)
            self.assertEqual(self.renderer.current_state, state)
            self.renderer.render_frame()
            buf = self.renderer.get_buffer()
            self.assertEqual(len(buf), self.renderer.buffer_size)
            # Frame should contain non-black face pixels
            self.assertNotEqual(buf, bytes(self.renderer.buffer_size))

    def test_idle_subcategory_rotation(self) -> None:
        self.renderer.set_state(RobotState.IDLE)
        initial_cat = self.renderer.current_idle_category
        self.renderer.render_frame()

        if self.renderer.idle_categories_count > 1:
            next_cat = self.renderer.rotate_idle_category(forward=True)
            self.assertNotEqual(initial_cat, next_cat)
            self.renderer.render_frame()

    def test_create_face_renderer_factory(self) -> None:
        # Physical LCD backend
        cfg_phys = DisplayConfig(backend="physical_lcd", width=480, height=320)
        renderer_phys = create_face_renderer(cfg_phys, mock=True)
        self.assertIsInstance(renderer_phys, PhysicalFaceRenderer)
        self.assertEqual(renderer_phys.width, 480)
        self.assertEqual(renderer_phys.height, 320)

        # Simulator backend
        cfg_sim = DisplayConfig(backend="simulator", width=800, height=480)
        renderer_sim = create_face_renderer(cfg_sim)
        self.assertIsInstance(renderer_sim, FaceSimulator)
        self.assertEqual(renderer_sim.width, 800)
        self.assertEqual(renderer_sim.height, 480)


if __name__ == "__main__":
    unittest.main()
