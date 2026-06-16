"""Safe encoding utilities."""

import base64
import json
from typing import Any
from urllib.parse import quote


def encode_base64(value: str) -> str:
    """Base64-encode text."""
    return base64.b64encode(value.encode()).decode()


def encode_url(value: str) -> str:
    """URL-encode text."""
    return quote(value, safe="")


def encode_json(value: Any) -> str:
    """JSON-encode a value."""
    return json.dumps(value, separators=(",", ":"), default=str)
