"""Image Generation Provider abstraction and OmniRoute implementation for Bimo."""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import io
import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedImage:
    """Represents the binary output and metadata of an image generation request."""

    data: bytes
    format: str  # "png" or "jpeg"
    width: int
    height: int
    prompt: str
    provider_name: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def image_bytes(self) -> bytes:
        return self.data

    @property
    def mime_type(self) -> str:
        fmt = self.format.lower()
        if fmt in ("jpg", "jpeg"):
            return "image/jpeg"
        if fmt == "svg":
            return "image/svg+xml"
        return "image/png"


class ImageGenerationProvider(ABC):
    """Abstract interface for Bimo image generation providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Friendly provider identifier."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether the image provider is currently reachable and configured."""
        pass

    @abstractmethod
    def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        format: str = "png",
    ) -> GeneratedImage:
        """Generate an image from natural language prompt."""
        pass


class UnavailableImageProvider(ImageGenerationProvider):
    """Fallback provider when no remote or AI image generation backend is configured."""

    def __init__(self, message: str = "AI Image generation backend is not configured or enabled.") -> None:
        self.message = message

    @property
    def provider_name(self) -> str:
        return "unavailable"

    def is_available(self) -> bool:
        return False

    def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        format: str = "png",
    ) -> GeneratedImage:
        raise RuntimeError(
            f"{self.message} Use 'image.render_local' for offline charts, diagrams, and posters."
        )


class OmniRouteImageProvider(ImageGenerationProvider):
    """Generates images targeting OmniRoute's OpenAI-compatible /v1/images/generations endpoint."""

    def __init__(
        self,
        base_url: str = "http://localhost:20128",
        model: str = "antigravity/gemini-3.1-flash-image",
        api_key: str = "",
        timeout_seconds: float = 120.0,
    ) -> None:
        url = base_url.strip().rstrip("/")
        if url.endswith("/v1"):
            url = url[:-3]
        self.base_url = url
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self._endpoint_url = f"{self.base_url}/v1/images/generations"

    @property
    def provider_name(self) -> str:
        return f"omniroute ({self.model})"

    def is_available(self) -> bool:
        """Quick probe to test if gateway endpoint is reachable."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/v1/models",
                headers={"User-Agent": "Bimo-Robot/1.0"},
            )
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        format: str = "png",
    ) -> GeneratedImage:
        if not prompt or not prompt.strip():
            raise ValueError("Image generation prompt cannot be empty.")

        body = {
            "model": self.model,
            "prompt": prompt.strip(),
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }
        payload = json.dumps(body).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Bimo-Robot/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = urllib.request.Request(
            self._endpoint_url,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            err_body = ""
            try:
                err_body = http_err.read().decode("utf-8")
            except Exception:
                pass
            raise RuntimeError(
                f"OmniRoute image generation HTTP {http_err.code}: {http_err.reason} ({err_body[:200]})"
            ) from http_err
        except urllib.error.URLError as url_err:
            raise RuntimeError(
                f"OmniRoute image gateway unreachable: {url_err.reason}"
            ) from url_err

        data_list = resp_data.get("data", [])
        if not data_list:
            raise RuntimeError("OmniRoute returned empty image data.")

        item = data_list[0]
        raw_bytes: bytes

        if "b64_json" in item:
            raw_bytes = base64.b64decode(item["b64_json"])
        elif "url" in item:
            with urllib.request.urlopen(item["url"], timeout=self.timeout_seconds) as img_resp:
                raw_bytes = img_resp.read()
        else:
            raise RuntimeError("Unexpected image response format from OmniRoute.")

        # Parse width/height safely with PIL
        width, height = 1024, 1024
        fmt = format.lower().lstrip(".")
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw_bytes))
            width, height = img.size
            if img.format:
                fmt = img.format.lower()
        except Exception:
            pass

        return GeneratedImage(
            data=raw_bytes,
            format=fmt,
            width=width,
            height=height,
            prompt=prompt.strip(),
            provider_name=self.provider_name,
            metadata={"model": self.model},
        )


class MockImageProvider(ImageGenerationProvider):
    """Deterministic mock image generator for testing and offline environments."""

    def __init__(self, provider_name: str = "mock_image_provider") -> None:
        self._name = provider_name
        self._call_count = 0

    @property
    def provider_name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        format: str = "png",
    ) -> GeneratedImage:
        self._call_count += 1
        from PIL import Image, ImageDraw

        w, h = 512, 512
        if "x" in size:
            try:
                parts = size.split("x")
                w, h = int(parts[0]), int(parts[1])
            except Exception:
                pass

        img = Image.new("RGB", (w, h), color=(30, 41, 59))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, w - 10, h - 10], outline=(56, 189, 248), width=3)
        draw.text((20, 20), f"Bimo Generated Image\nPrompt: {prompt[:50]}", fill=(241, 245, 249))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()

        return GeneratedImage(
            data=data,
            format="png",
            width=w,
            height=h,
            prompt=prompt,
            provider_name=self._name,
            metadata={"mock": True, "call_count": self._call_count},
        )
