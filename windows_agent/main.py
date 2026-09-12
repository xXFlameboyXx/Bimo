"""Command-line entry point for running the Windows PC Agent independently.

Usage:
    python -m windows_agent [--host 0.0.0.0] [--port 8088] [--secret YOUR_SECRET]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from bimo.core.config import Config
from windows_agent.commands import build_default_command_registry
from windows_agent.server import PCAgentServerManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("windows_agent")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bimo Windows PC Agent (Phase 7)")
    parser.add_argument("--host", default=None, help="Host address to bind to (e.g. 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on (e.g. 8088)")
    parser.add_argument("--secret", default=None, help="Shared HMAC secret for authentication")
    parser.add_argument(
        "--allowed-apps",
        default=None,
        help="Comma-separated allowlisted applications (e.g. notepad,calculator,explorer)",
    )

    args = parser.parse_args()

    cfg = Config.from_env()

    host = args.host or cfg.pc_agent.host or "0.0.0.0"
    port = args.port or cfg.pc_agent.port or 8088
    secret = args.secret or cfg.pc_agent.shared_secret
    apps = (
        [a.strip().lower() for a in args.allowed_apps.split(",") if a.strip()]
        if args.allowed_apps
        else cfg.pc_agent.allowed_apps
    )

    if not secret:
        logger.error("ERROR: No shared secret provided! Set PC_AGENT_SHARED_SECRET or pass --secret.")
        sys.exit(1)

    logger.info("Starting Bimo Windows PC Agent...")
    logger.info("Host: %s | Port: %d", host, port)
    logger.info("Allowed Applications: %s", ", ".join(apps))
    logger.info("Authentication: HMAC-SHA256 (Shared Secret configured)")

    registry = build_default_command_registry(allowed_apps=apps)
    manager = PCAgentServerManager(
        host=host,
        port=port,
        shared_secret=secret,
        max_clock_skew=cfg.pc_agent.max_clock_skew,
        registry=registry,
    )

    try:
        manager.start(background=False)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal. Shutting down PC Agent.")
        manager.stop()


if __name__ == "__main__":
    main()
