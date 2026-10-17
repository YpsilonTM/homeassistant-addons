from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")
INT32_MAX = 2147483647


@dataclass(frozen=True)
class Tag:
    id: int
    access: str
    slug: str
    name: str
    name_zh: str
    unit: str
    increase: int
    size: int
    dtype: str
    scale_or_max: int
    node: str
    platform: str
    enabled_by_default: bool
    fast: bool
    secret: bool
    dangerous: bool

    @property
    def words(self) -> int:
        return max(1, self.size // 2)

    @property
    def scale(self) -> int:
        if self.increase > 1:
            return self.increase
        if 1 < self.scale_or_max < 100000:
            return self.scale_or_max
        return 1

    @property
    def writable(self) -> bool:
        return self.access in {"RW", "CMD"}

    def decode(self, raw: bytes) -> Any:
        if self.dtype == "bytes":
            return raw.hex()
        if self.dtype == "string":
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")
        signed = self.dtype.startswith("int") and "uint" not in self.dtype
        if len(raw) >= 8:
            value = struct.unpack("<q" if signed else "<Q", raw[:8])[0]
        elif len(raw) >= 4:
            value = struct.unpack("<i" if signed else "<I", raw[:4])[0]
        elif len(raw) >= 2:
            value = struct.unpack("<h" if signed else "<H", raw[:2])[0]
        else:
            return raw.hex()
        if self.scale > 1:
            return value / self.scale
        return value

    def encode(self, value: Any) -> bytes:
        if self.dtype == "string":
            text = "" if value is None else str(value)
            data = text.encode("utf-8")[: self.size]
            return data.ljust(self.size, b"\x00")
        if self.dtype == "bytes":
            blob = bytes.fromhex(str(value).replace(":", ""))
            if len(blob) > self.size:
                raise ValueError(f"hex value longer than {self.size} bytes")
            return blob.ljust(self.size, b"\x00")
        number = float(value)
        raw_int = int(round(number * self.scale))
        signed = self.dtype.startswith("int") and "uint" not in self.dtype
        if self.size >= 8:
            return struct.pack("<q" if signed else "<Q", raw_int)
        if self.size >= 4:
            return struct.pack("<i" if signed else "<I", raw_int)
        return struct.pack("<h" if signed else "<H", raw_int)


def load_catalog(path: Path | None = None) -> list[Tag]:
    payload = json.loads((path or CATALOG_PATH).read_text(encoding="utf-8"))
    return [Tag(**row) for row in payload["tags"]]


def tags_by_id(tags: list[Tag] | None = None) -> dict[int, Tag]:
    return {tag.id: tag for tag in (tags or load_catalog())}
