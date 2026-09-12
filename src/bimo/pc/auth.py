"""HMAC-SHA256 Authentication and replay protection for the PC Agent protocol.

Provides canonical serialization, cryptographic signing, constant-time verification,
timestamp clock skew checking, and nonce replay cache.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import threading
import time
from typing import Any, Dict, Optional, Set, Tuple

from bimo.pc.models import PCErrorCode


def generate_canonical_payload(
    version: int,
    request_id: str,
    timestamp: float,
    nonce: str,
    command: str,
    arguments: Dict[str, Any],
) -> bytes:
    """Generate the canonical byte sequence for signing.

    Format:
        version|request_id|timestamp_str|nonce|command|sorted_arguments_json
    """
    args_json = json.dumps(arguments, sort_keys=True, separators=(",", ":"))
    # Format timestamp to 3 decimal places for stability
    ts_str = f"{timestamp:.3f}"
    canonical_str = f"{version}|{request_id}|{ts_str}|{nonce}|{command}|{args_json}"
    return canonical_str.encode("utf-8")


def sign_request(
    shared_secret: str,
    version: int,
    request_id: str,
    timestamp: float,
    nonce: str,
    command: str,
    arguments: Dict[str, Any],
) -> str:
    """Compute HMAC-SHA256 signature for a request."""
    if not shared_secret:
        return ""
    key = shared_secret.encode("utf-8")
    payload = generate_canonical_payload(version, request_id, timestamp, nonce, command, arguments)
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify_signature(
    shared_secret: str,
    expected_signature: str,
    version: int,
    request_id: str,
    timestamp: float,
    nonce: str,
    command: str,
    arguments: Dict[str, Any],
) -> bool:
    """Verify HMAC-SHA256 signature using constant-time comparison."""
    if not shared_secret or not expected_signature:
        return False
    computed = sign_request(
        shared_secret=shared_secret,
        version=version,
        request_id=request_id,
        timestamp=timestamp,
        nonce=nonce,
        command=command,
        arguments=arguments,
    )
    return hmac.compare_digest(computed.lower(), expected_signature.lower())


class ReplayCache:
    """Thread-safe TTL-based nonce and request_id replay cache."""

    def __init__(self, max_skew_seconds: float = 30.0) -> None:
        self.max_skew_seconds = max_skew_seconds
        self._lock = threading.Lock()
        # Mapping from (nonce, request_id) -> expiry_time
        self._seen: Dict[Tuple[str, str], float] = {}

    def is_replayed_or_record(self, nonce: str, request_id: str, current_time: Optional[float] = None) -> bool:
        """Check if nonce/request_id combination was already seen.

        If already seen, returns True (replayed).
        If new, records it and returns False (not replayed).
        """
        now = current_time if current_time is not None else time.time()
        key = (nonce, request_id)

        with self._lock:
            self._purge_expired(now)
            if key in self._seen:
                return True
            # Expire after 2 * max_skew_seconds
            self._seen[key] = now + (self.max_skew_seconds * 2.0)
            return False

    def _purge_expired(self, now: float) -> None:
        expired_keys = [k for k, exp in self._seen.items() if exp <= now]
        for k in expired_keys:
            del self._seen[k]

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._seen.clear()


class RequestAuthenticator:
    """Validates incoming requests for signature correctness, clock skew, and replay attacks."""

    def __init__(self, shared_secret: str, max_clock_skew: float = 30.0) -> None:
        self.shared_secret = shared_secret
        self.max_clock_skew = max_clock_skew
        self.replay_cache = ReplayCache(max_skew_seconds=max_clock_skew)

    def authenticate(
        self,
        version: int,
        request_id: str,
        timestamp: float,
        nonce: str,
        command: str,
        arguments: Dict[str, Any],
        signature: str,
        current_time: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """Validate request authentication.

        Returns:
            (is_valid, error_code, error_message)
        """
        if not self.shared_secret:
            return False, PCErrorCode.AUTH_FAILED.value, "Server shared secret not configured"

        if not signature:
            return False, PCErrorCode.AUTH_FAILED.value, "Missing request signature"

        if not nonce:
            return False, PCErrorCode.AUTH_FAILED.value, "Missing request nonce"

        if not request_id:
            return False, PCErrorCode.INVALID_PAYLOAD.value, "Missing request_id"

        now = current_time if current_time is not None else time.time()

        # Clock skew validation
        if abs(now - timestamp) > self.max_clock_skew:
            return (
                False,
                PCErrorCode.EXPIRED_TIMESTAMP.value,
                f"Timestamp delta {abs(now - timestamp):.1f}s exceeds allowable skew of {self.max_clock_skew}s",
            )

        # Signature verification (constant-time)
        if not verify_signature(
            shared_secret=self.shared_secret,
            expected_signature=signature,
            version=version,
            request_id=request_id,
            timestamp=timestamp,
            nonce=nonce,
            command=command,
            arguments=arguments,
        ):
            return False, PCErrorCode.AUTH_FAILED.value, "Invalid HMAC signature"

        # Nonce replay protection
        if self.replay_cache.is_replayed_or_record(nonce, request_id, current_time=now):
            return False, PCErrorCode.REPLAY_DETECTED.value, "Replay attack detected: nonce or request_id reused"

        return True, None, None
