"""QueryValidator — cổng kiểm soát đứng trước mọi truy vấn (Tuần 4).

[FILE MỚI]  Đích thật: app/domain/validator.py
Hướng dẫn:  ../W4-Huong-Dan-Thuc-Hien.md §2   ·   Quyết định: docs/adr/ADR-007

VỊ TRÍ TRONG KIẾN TRÚC — đọc kỹ để không hiểu lầm:
  Pydantic (ở biên HTTP) là CỔNG THỨ NHẤT: từ chối filter lạ (extra="forbid"),
  ép kiểu, chặn limit>100 (le=100). QueryValidator là LỚP CHÍNH SÁCH đằng sau đó.
  Với /jobs/search, phần lớn kiểm tra của validator là 'phòng thủ theo chiều sâu'
  (trùng với Pydantic, nhưng bảo vệ cả lời gọi KHÔNG qua HTTP — vd gọi nội bộ, hoặc
  khi model bị nới lỏng sau này). Giá trị RIÊNG của validator, không Pydantic nào
  thay được, gồm:
    1) allowlist CỘT TRẢ RA (default-deny)  — assert_safe_columns()
    2) ngưỡng k-anonymity cho metric lương   — suppress_if_small()  [áp dụng Tuần 5]
    3) allowlist dimension/metric            — cho /market/metrics  [Tuần 5]
    4) cơ chế required-filter                — check_required()      [rỗng, xem ADR-007]
  Mỗi chính sách là một method riêng để TEST ĐƯỢC độc lập với Pydantic.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.domain.catalog import FILTER_NAMES, MAX_LIMIT
from app.errors import (
    InvalidFilterError,
    InvalidRequestError,
    LimitExceededError,
    MissingRequiredFilterError,
)
from app.models.jobs import JobItem, SearchRequest

# Cột được phép TRẢ RA = đúng schema JobItem. Mặc định từ chối mọi cột khác.
# GIẢI THÍCH: lấy từ JobItem.model_fields để allowlist và hợp đồng KHÔNG BAO GIỜ lệch.
ALLOWED_RETURN_COLUMNS: frozenset[str] = frozenset(JobItem.model_fields)

# Chốt chặn PII: các mảnh tên cột tuyệt đối không được xuất hiện trong output.
# Kiểm tra chéo, phòng khi ai đó lỡ thêm cột nhạy cảm vào JobItem.
PII_DENYLIST: frozenset[str] = frozenset(
    {"email", "password", "dob", "address", "contactno", "phone", "hash", "resume", "candidate"}
)


class QueryValidator:
    """Cổng kiểm soát. Stateless — có thể tạo một instance dùng chung."""

    ALLOWED_DIMENSIONS: frozenset[str] = frozenset({"source", "seniority", "category"})   # /market/metrics
    ALLOWED_METRICS: frozenset[str] = frozenset(
        {"median_salary_vnd_month", "posting_count", "salary_disclosed_count", "salary_sample_count"}
    )
    REQUIRED_FILTERS: frozenset[str] = frozenset({"posted_after"})   # bắt buộc (ADR-020)
    MAX_LIMIT: int = MAX_LIMIT
    MIN_GROUP_SIZE: int = 5                            # k-anonymity (ngưỡng mặc định; settings ghi đè)

    # ---- các kiểm tra hạt nhỏ (test được độc lập với Pydantic) ----
    def check_limit(self, limit: int) -> None:
        if limit > self.MAX_LIMIT:
            raise LimitExceededError(f"limit vượt mức cho phép ({self.MAX_LIMIT})", field="limit")

    def check_filters(self, active_names: Iterable[str]) -> None:
        unknown = set(active_names) - FILTER_NAMES
        if unknown:
            name = sorted(unknown)[0]
            raise InvalidFilterError(f"filter không hợp lệ: {name}", field=f"filters.{name}")

    def check_required(self, active_names: Iterable[str]) -> None:
        missing = self.REQUIRED_FILTERS - set(active_names)
        if missing:
            name = sorted(missing)[0]
            raise MissingRequiredFilterError(f"thiếu filter bắt buộc: {name}", field=f"filters.{name}")

    # ---- điều phối cho /jobs/search ----
    def validate_search(self, req: SearchRequest) -> None:
        """Gọi ở đầu handler /jobs/search, TRƯỚC khi chạm repository."""
        self.check_limit(req.limit)
        active = req.filters.active_names()
        self.check_filters(active)
        self.check_required(active)

    # ---- dành cho /market/metrics (Tuần 5) ----
    def validate_dimension(self, dimension: str) -> None:
        if dimension not in self.ALLOWED_DIMENSIONS:
            raise InvalidRequestError(f"dimension không hợp lệ: {dimension}", field="dimension")

    def validate_metric(self, metric: str) -> None:
        if metric not in self.ALLOWED_METRICS:
            raise InvalidRequestError(f"metric không hợp lệ: {metric}", field="metric")

    def suppress_if_small(self, sample_count: int, value, *, min_size: int | None = None):
        """k-anonymity: median che (trả None) khi SỐ MẪU LƯƠNG quá nhỏ.

        GIẢI THÍCH: ngưỡng dựa trên salary_sample_count (số job có đủ min&max), KHÔNG phải
        posting_count — một nhóm 100 job nhưng chỉ 3 có lương thì median vẫn suy ngược được
        (ADR-020). min_size mặc định MIN_GROUP_SIZE; handler truyền settings.metrics_min_sample_size.
        """
        threshold = self.MIN_GROUP_SIZE if min_size is None else min_size
        return None if sample_count < threshold else value

    # ---- default-deny cột trả ra ----
    def assert_safe_columns(self, column_names: Iterable[str]) -> None:
        """Chặn rò rỉ: output chỉ được chứa cột trong ALLOWED_RETURN_COLUMNS."""
        cols = {c.lower() for c in column_names}
        extra = cols - {c.lower() for c in ALLOWED_RETURN_COLUMNS}
        if extra:
            raise InvalidRequestError(f"cột không được phép trả ra: {sorted(extra)}")
        leaking = cols & PII_DENYLIST
        if leaking:
            raise InvalidRequestError(f"phát hiện cột PII trong output: {sorted(leaking)}")
