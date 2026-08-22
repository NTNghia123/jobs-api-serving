"""page_token: mờ với consumer, và ĐƯỢC KÝ.

Vì sao phải ký:
  page_token là dữ liệu do người gọi gửi lên. Nếu nó chỉ là base64 của JSON,
  người gọi sửa được nội dung bên trong. Khi Tuần 3 dùng nội dung đó để dựng
  mệnh đề WHERE, một token bị sửa sẽ đi vòng qua toàn bộ allowlist —
  tức là một lỗ injection nằm ngay trong cơ chế phân trang.

Token cũng gắn "vân tay" của bộ filter: nếu consumer đổi filter mà vẫn dùng
token cũ, ta từ chối thay vì trả kết quả trộn lẫn hai truy vấn khác nhau.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from app.errors import InvalidPageTokenError

_SEP = "."


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except Exception as exc:  # noqa: BLE001
        raise InvalidPageTokenError("page_token không hợp lệ", field="page_token") from exc


def filters_fingerprint(payload: dict[str, Any]) -> str:
    """Vân tay của bộ filter — đổi filter thì token cũ hết hiệu lực."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def encode_page_token(cursor: dict[str, Any], *, fingerprint: str, secret: str) -> str:
    body = {"c": cursor, "f": fingerprint}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    payload = _b64e(raw)
    sig = _b64e(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}{_SEP}{sig}"


def decode_page_token(token: str, *, fingerprint: str, secret: str) -> dict[str, Any]:
    if token.count(_SEP) != 1:
        raise InvalidPageTokenError("page_token sai định dạng", field="page_token")
    payload, sig = token.split(_SEP)
    expected = _b64e(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    # compare_digest: so sánh thời gian hằng định, tránh timing attack
    if not hmac.compare_digest(sig, expected):
        raise InvalidPageTokenError("page_token đã bị sửa đổi", field="page_token")
    try:
        body = json.loads(_b64d(payload))
    except json.JSONDecodeError as exc:
        raise InvalidPageTokenError("page_token không đọc được", field="page_token") from exc
    if body.get("f") != fingerprint:
        raise InvalidPageTokenError(
            "page_token thuộc về một bộ filter khác — hãy gọi lại từ trang đầu",
            field="page_token",
        )
    return body.get("c", {})
