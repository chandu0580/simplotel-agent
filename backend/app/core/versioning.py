import hashlib
import json
from typing import Any


def content_hash(value: Any, length: int = 12) -> str:
    """Stable short hash of JSON-serialisable content, used for prompt/tool/knowledge versions."""
    encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]
