"""Phát một API key mới: in client_id + key thô (MỘT LẦN) và hash để bỏ vào config.

[FILE MỚI - TUẦN 6]
Cách chạy:  python -m scripts.issue_key team-ai
            python -m scripts.issue_key team-ai --days 90

GIẢI THÍCH: server chỉ lưu HASH. Key thô hiện đúng một lần ở đây để đưa cho consumer
(đây chính là 'test token' cho Tuần 8). Mất key thô thì phát cái mới, không khôi phục được.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone


def main() -> None:
    ap = argparse.ArgumentParser(description="Phát API key cho một client.")
    ap.add_argument("client_id", help="Định danh client, vd 'team-ai'.")
    ap.add_argument("--days", type=int, default=None, help="Số ngày hết hạn (mặc định: không hết hạn).")
    args = ap.parse_args()

    raw_key = secrets.token_urlsafe(32)                 # key thô ngẫu nhiên, mạnh
    key_sha256 = hashlib.sha256(raw_key.encode()).hexdigest()
    expires_at = None
    if args.days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()

    entry = {args.client_id: {"key_sha256": key_sha256, "expires_at": expires_at}}

    print("=== PHÁT KEY THÀNH CÔNG ===")
    print(f"client_id : {args.client_id}")
    print(f"API key   : {raw_key}      <-- ĐƯA CHO CONSUMER, chỉ hiện MỘT LẦN")
    print(f"hết hạn   : {expires_at or 'không'}")
    print()
    print("Thêm vào JOBS_API_API_KEYS (gộp nếu đã có key khác):")
    print(json.dumps(entry, ensure_ascii=False))


if __name__ == "__main__":
    main()
