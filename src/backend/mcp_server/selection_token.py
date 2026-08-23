"""Short-lived, self-contained references to one exact MCP search result."""

import base64
import hashlib
import hmac
import json
import os
import time
import zlib

TOKEN_VERSION = 1
TOKEN_TTL_SECONDS = 300
_DEFAULT_SIGNING_KEY = b"movie-seat-finder-selection-token-v1"


def _signing_key():
    configured = os.environ.get("MCP_SELECTION_SECRET", "").encode("utf-8")
    return configured or _DEFAULT_SIGNING_KEY


def _encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_selection_token(payload, now=None):
    """Encode one result without exposing a multi-argument reconstruction task."""
    issued_at = int(time.time() if now is None else now)
    body = {
        "version": TOKEN_VERSION,
        "issuedAt": issued_at,
        "expiresAt": issued_at + TOKEN_TTL_SECONDS,
        **payload,
    }
    serialized = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    compressed = zlib.compress(serialized, level=9)
    signature = hmac.new(_signing_key(), compressed, hashlib.sha256).digest()[:16]
    return f"{_encode(compressed)}.{_encode(signature)}"


def read_selection_token(token, now=None, allow_expired=False):
    """Validate and decode a selection token, rejecting stale or altered input."""
    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        compressed = _decode(encoded_payload)
        signature = _decode(encoded_signature)
        expected = hmac.new(_signing_key(), compressed, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        payload = json.loads(zlib.decompress(compressed))
    except (ValueError, TypeError, json.JSONDecodeError, zlib.error) as error:
        raise ValueError("That seat-map selection is invalid. Run find_movie_seats again.") from error

    current_time = int(time.time() if now is None else now)
    if payload.get("version") != TOKEN_VERSION:
        raise ValueError("That seat-map selection is invalid. Run find_movie_seats again.")
    payload["tokenExpired"] = payload.get("expiresAt", 0) < current_time
    if payload["tokenExpired"] and not allow_expired:
        raise ValueError("That seat-map selection expired. Run find_movie_seats again for live availability.")
    return payload
