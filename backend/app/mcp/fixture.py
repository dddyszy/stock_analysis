"""回放 probe.py 保存的真实响应样本，用于测试解析器、在未授权时联调。"""

import json
from pathlib import Path
from typing import Any

from app.core.config import BACKEND_DIR
from app.mcp.client import unwrap_payload
from app.mcp.errors import McpBusinessError

FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures" / "mcp"


class FixtureSession:
    def __init__(self, fixture_dir: Path = FIXTURE_DIR) -> None:
        self.fixture_dir = fixture_dir

    def available(self, tool: str) -> bool:
        return any(self.fixture_dir.glob(f"{tool}*.json"))

    async def call(self, tool: str, args: dict | None = None) -> Any:
        candidates = sorted(self.fixture_dir.glob(f"{tool}__*.json")) + sorted(self.fixture_dir.glob(f"{tool}.json"))
        if not candidates:
            raise McpBusinessError(f"没有 {tool} 的样本")
        doc = json.loads(candidates[0].read_text(encoding="utf-8"))
        return unwrap_payload(doc.get("payload", doc))

    async def close(self) -> None:
        return None
