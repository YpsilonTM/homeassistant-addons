from __future__ import annotations

import logging
import socket
import ssl
import struct
import threading
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from src.catalog import Tag

LOGGER = logging.getLogger(__name__)

NEAR_PORT = 21319
AES_KEY = b"/A)G$SFQzj4mQtyI(hhli@41pKw#d`3Q"
PEER_PATH = b"\x02"
EVAC_PATH = b"\x01\x01"
METER_PATH = b"\x01\x01\x01"
NODE_PATHS = {"evac": EVAC_PATH, "meter": METER_PATH}
READ_CHUNK = 12
EXCEPTION_NAMES = {
    2: "illegalParameter",
    11: "targetDeviceNotFound",
    22: "unauthorized",
}


class NearError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.raw = raw


def u16(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def aes_encrypt(plain: bytes) -> bytes:
    iv = time.time_ns().to_bytes(8, "little") + time.time_ns().to_bytes(8, "big")
    iv = iv[:16]
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).encryptor()
    return iv + encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(blob: bytes) -> bytes:
    if len(blob) < 32 or len(blob) % 16:
        raise NearError(f"ciphertext length {len(blob)}")
    iv, ciphertext = blob[:16], blob[16:]
    decryptor = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv)).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    pad = padded[-1]
    if not (1 <= pad <= 16) or padded[-pad:] != bytes([pad]) * pad:
        return padded
    return padded[:-pad]


def build_frame(seq: int, extra: bytes, x: bytes, y: bytes, encrypted: bool) -> bytes:
    flags = ((1 if encrypted else 0) << 7) | ((len(x) & 7) << 3) | (len(y) & 7)
    length = 1 + len(x) + len(y) + len(extra)
    return u16(seq) + u16(NEAR_PORT) + u16(length) + bytes([flags]) + x + y + extra


def parse_frame(data: bytes) -> dict[str, Any]:
    if len(data) < 7:
        raise NearError(f"short frame {data.hex()}")
    seq, magic, length, flags = struct.unpack_from("<HHHB", data, 0)
    x_len, y_len, encrypted = (flags >> 3) & 7, flags & 7, bool((flags >> 7) & 1)
    offset = 7
    x, offset = data[offset : offset + x_len], offset + x_len
    y, offset = data[offset : offset + y_len], offset + y_len
    extra = data[offset:]
    body = extra
    if encrypted and extra:
        body = aes_decrypt(extra)
    return {
        "seq": seq,
        "magic": magic,
        "length": length,
        "encrypted": encrypted,
        "x": x,
        "y": y,
        "body": body,
    }


def build_batch_read(tags: list[Tag]) -> bytes:
    inner = bytearray([len(tags)])
    for tag in tags:
        inner += u16(tag.id)
        inner.append(tag.words)
    return bytes([3]) + u16(1 + 3 * len(tags)) + bytes(inner)


def build_batch_write(tag: Tag, raw: bytes) -> bytes:
    if len(raw) != tag.size:
        raise NearError(f"write size {len(raw)} != {tag.size} for tag {tag.id}")
    inner = bytes([1]) + u16(tag.id) + bytes([tag.words]) + raw
    return bytes([4, 1 + 3 + tag.size]) + inner


def decode_batch_read(body: bytes) -> dict[int, bytes]:
    if not body:
        raise NearError("empty batchRead reply")
    cmd = body[0] & 127
    if cmd == 31:
        code = body[3] if len(body) > 3 else None
        name = EXCEPTION_NAMES.get(code, str(code))
        raise NearError(f"exceptionalResp {name}", code=code, raw=body.hex())
    if cmd != 3 or len(body) < 3:
        raise NearError(f"unexpected batchRead cmd={cmd} body={body.hex()}")
    values: dict[int, bytes] = {}
    offset = 3
    while offset + 3 <= len(body):
        tag_id = struct.unpack_from("<H", body, offset)[0]
        words = body[offset + 2]
        offset += 3
        values[tag_id] = body[offset : offset + words * 2]
        offset += words * 2
    return values


def decode_batch_write(body: bytes) -> dict[str, Any]:
    if not body:
        raise NearError("empty batchWrite reply")
    cmd = body[0] & 127
    if cmd == 31:
        code = body[3] if len(body) > 3 else None
        name = EXCEPTION_NAMES.get(code, str(code))
        raise NearError(f"exceptionalResp {name}", code=code, raw=body.hex())
    if cmd != 4:
        raise NearError(f"unexpected batchWrite cmd={cmd} body={body.hex()}")
    return {"cmd": cmd, "raw": body.hex()}


def parse_ident_nodes(body: bytes) -> list[dict[str, Any]]:
    if not body or (body[0] & 127) != 51:
        return []
    offset = 7
    count = struct.unpack_from("<H", body, 5)[0] if len(body) >= 7 else 0
    nodes: list[dict[str, Any]] = []
    for _ in range(count):
        if offset >= len(body):
            break
        prefix = body[offset]
        offset += 1
        path_len = prefix & 127
        path = body[offset : offset + path_len]
        offset += path_len
        if offset >= len(body):
            nodes.append({"path": path.hex(), "attrs": []})
            break
        attr_len = body[offset]
        offset += 1
        attrs: list[dict[str, str]] = []
        end = offset + attr_len
        while offset + 2 <= end and offset + 2 <= len(body):
            kind = body[offset]
            length = body[offset + 1]
            offset += 2
            value = body[offset : offset + length]
            offset += length
            text = (
                value.decode("ascii", "replace")
                if all(32 <= byte < 127 for byte in value)
                else value.hex()
            )
            attrs.append({"k": str(kind), "v": text})
        nodes.append({"path": path.hex(), "attrs": attrs})
    return nodes


class NearClient:
    def __init__(
        self,
        host: str,
        port: int,
        cert_path: Path,
        key_path: Path,
        timeout: float = 6.0,
    ) -> None:
        self.host = host
        self.port = port
        self.cert_path = cert_path
        self.key_path = key_path
        self.timeout = timeout
        self._sock: ssl.SSLSocket | None = None
        self._seq = 1
        self._lock = threading.Lock()
        self.serial: str | None = None
        self.model: str | None = None
        self.firmware: str | None = None

    def close(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is None:
            return
        try:
            sock.close()
        except OSError:
            pass

    def connect(self) -> None:
        self.close()
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.load_cert_chain(str(self.cert_path), str(self.key_path))
        sock = context.wrap_socket(raw, server_hostname=self.host)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._seq = 1
        self._handshake()
        LOGGER.info(
            "near_connected host=%s tls=%s cipher=%s serial=%s",
            self.host,
            sock.version(),
            sock.cipher(),
            self.serial,
        )

    def ensure_connected(self) -> None:
        if self._sock is None:
            self.connect()

    def read_tags(self, tags: list[Tag]) -> dict[int, Any]:
        values: dict[int, Any] = {}
        by_node: dict[str, list[Tag]] = {}
        for tag in tags:
            by_node.setdefault(tag.node, []).append(tag)
        with self._lock:
            self.ensure_connected()
            try:
                for node, group in by_node.items():
                    path = NODE_PATHS.get(node)
                    if path is None:
                        raise NearError(f"unknown node {node}")
                    for start in range(0, len(group), READ_CHUNK):
                        chunk = group[start : start + READ_CHUNK]
                        values.update(self._read_chunk(path, chunk))
            except Exception:
                self.close()
                raise
        return values

    def write_tag(self, tag: Tag, value: Any) -> None:
        path = NODE_PATHS.get(tag.node)
        if path is None:
            raise NearError(f"unknown node {tag.node}")
        raw = tag.encode(value)
        payload = build_batch_write(tag, raw)
        with self._lock:
            self.ensure_connected()
            try:
                parsed = self._request(payload, path, encrypted=True)
                decode_batch_write(parsed["body"])
            except Exception:
                self.close()
                raise
        LOGGER.info("near_write tag=%s slug=%s value=%r", tag.id, tag.slug, value)

    def _handshake(self) -> None:
        self._request(bytes([50, 2, 2, 0]), b"\x01", encrypted=True)
        parsed = self._request(bytes([51, 0, 0]), b"\x01", encrypted=False)
        nodes = parse_ident_nodes(parsed["body"])
        for node in nodes:
            if node.get("path") != "0101":
                continue
            for attr in node.get("attrs", []):
                text = attr.get("v") or ""
                if text.startswith("12") and len(text) >= 10 and text.isalnum():
                    self.serial = text
                elif text.startswith("V") and "SPC" in text:
                    self.firmware = text
                elif "EVAC" in text or "Sigen" in text:
                    self.model = text

    def _read_chunk(self, path: bytes, tags: list[Tag]) -> dict[int, Any]:
        parsed = self._request(build_batch_read(tags), path, encrypted=True)
        raw_values = decode_batch_read(parsed["body"])
        decoded: dict[int, Any] = {}
        for tag in tags:
            blob = raw_values.get(tag.id)
            if blob is None:
                continue
            decoded[tag.id] = tag.decode(blob)
        return decoded

    def _request(self, extra: bytes, x: bytes, encrypted: bool) -> dict[str, Any]:
        sock = self._sock
        if sock is None:
            raise NearError("not connected")
        seq = self._seq
        self._seq = (self._seq + 1) & 0xFFFF or 1
        payload = aes_encrypt(extra) if encrypted else extra
        frame = build_frame(seq, payload, x, PEER_PATH, encrypted)
        sock.sendall(frame)
        reply = recv_frame(sock, self.timeout)
        if not reply:
            raise NearError("no reply")
        return parse_frame(reply)


def recv_frame(sock: ssl.SSLSocket, timeout: float) -> bytes:
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        if len(buf) >= 7:
            _seq, _magic, length = struct.unpack_from("<HHH", buf, 0)
            need = 6 + length
            if len(buf) >= need:
                return bytes(buf[:need])
    return bytes(buf)
