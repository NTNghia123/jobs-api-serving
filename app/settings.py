"""Cấu hình ứng dụng — đọc từ biến môi trường, không hard-code.

[FILE SỬA]  Đích thật: app/settings.py
So với bản Tuần 2, chỉ THÊM 2 field ở cuối (đánh dấu ★). Phần còn lại giữ nguyên.

Nguyên tắc: mọi thứ khác nhau giữa local / staging / prod đều phải nằm ở đây,
không nằm rải rác trong code. Xem README mục "Cấu hình".
"""
from datetime import datetime
from functools import lru_cache

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Backend kho khả dụng theo giai đoạn migration. Phase 3 thêm 'bigquery'; Phase 4 thêm 'duckdb'.
_AVAILABLE_BACKENDS: frozenset[str] = frozenset({"fake"})


# ★ THÊM Ở TUẦN 6 — một bản ghi key: server CHỈ lưu hash + hạn dùng, không lưu key thô.
class ApiKeyEntry(BaseModel):
    key_sha256: str
    expires_at: datetime | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="JOBS_API_",
        extra="ignore",
    )

    # --- môi trường ---
    env: str = Field(default="local", description="local | staging | prod")
    debug: bool = False

    # --- hợp đồng API ---
    api_version: str = "v1"
    service_name: str = "jobs-serving-api"

    # --- observability ---
    log_level: str = "INFO"
    log_format: str = Field(default="json", description="json | text (text chỉ dùng khi dev)")

    # --- phân trang & giới hạn truy vấn ---
    page_token_secret: str = Field(
        default="dev-only-insecure-secret",
        min_length=8,
        description="Khoá HMAC ký page_token. PROD phải lấy từ Secret Manager.",
    )

    # --- backend kho dữ liệu --- (migration: chỉ 'fake' khả dụng; bigquery→Phase 3, duckdb→Phase 4)
    warehouse_backend: str = Field(default="fake", description="fake (Phase 0) | bigquery (Phase 3) | duckdb (Phase 4)")

    # ★ THÊM Ở TUẦN 3 — đường dẫn file kho DuckDB (API đọc từ đây, read-only).
    duckdb_path: str = Field(
        default="analytics.duckdb",
        description="File kho phân tích cục bộ do job ELT ghi ra.",
    )

    # ★ THÊM Ở TUẦN 3 — chuỗi kết nối MySQL nguồn.
    # GIẢI THÍCH: CHỈ script ELT (app/elt/build_silver.py) đọc field này. Tầng API
    # (app/api/*) tuyệt đối KHÔNG import nó — API chỉ chạm DuckDB. Dùng user 'reader'
    # (GRANT SELECT) để dù ELT có bug cũng không sửa được nguồn.
    mysql_url: str = Field(
        default="mysql+pymysql://reader:reader_pw@localhost:3306/jobportal",
        description="User CHỈ-ĐỌC. Prod lấy mật khẩu từ Secret Manager, không hard-code.",
    )

    # ★ THÊM Ở TUẦN 5 — cache cho /market/metrics.
    cache_backend: str = Field(default="memory", description="memory | redis")
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Chỉ dùng khi cache_backend=redis. Cần một Redis server đang chạy.",
    )
    cache_ttl_seconds: int = Field(default=300, description="Thời gian sống của cache (giây).")

    # ★ THÊM Ở TUẦN 6 — xác thực API key.
    # Nạp từ env JOBS_API_API_KEYS dạng JSON: {"team-ai": {"key_sha256": "...", "expires_at": null}}
    # Rỗng + env=local → store tự seed key dev (xem config_key_store.py).
    api_keys: dict[str, ApiKeyEntry] = Field(default_factory=dict)

    # ★ THÊM Ở TUẦN 6 — rate-limit theo client (token bucket).
    rate_limit_per_minute: int = Field(default=120, ge=1, description="Số request/phút mỗi client.")
    rate_limit_burst: int = Field(default=0, ge=0, description="Sức chứa burst; 0 = bằng per_minute.")

    # ★ THÊM Ở TUẦN 7 — chọn adapter rate-limit: memory (1 instance) | redis (chia sẻ đa instance).
    rate_limiter_backend: str = Field(default="memory", description="memory | redis")

    # ★ MIGRATION (BigQuery) — ngưỡng k-anonymity cho median lương ở /market/metrics.
    metrics_min_sample_size: int = Field(
        default=5, ge=1,
        description="median_salary_vnd_month = null khi salary_sample_count < ngưỡng này.",
    )

    # ★ THÊM Ở TUẦN 7 — ngân sách timeout LỒNG NHAU: client > request > query.
    # Khai request_timeout_s TRƯỚC query_timeout_s để validator dưới thấy được nó qua info.data.
    request_timeout_s: int = Field(
        default=20, ge=1,
        description="Ngân sách xử lý 1 request (giây). Đặt ở tầng edge/Cloud Run; phải < client timeout.",
    )
    query_timeout_s: int = Field(
        default=10, ge=1,
        description="Trần thời gian 1 truy vấn kho (giây). Cưỡng chế THẬT ở DuckDB adapter (interrupt).",
    )

    @field_validator("query_timeout_s")
    @classmethod
    def _query_must_nest_under_request(cls, v: int, info):
        # Bất biến lồng nhau: query < request. Sai thứ tự -> KHÔNG boot (fail-fast).
        req = (info.data or {}).get("request_timeout_s")
        if req is not None and v >= req:
            raise ValueError(
                "JOBS_API_QUERY_TIMEOUT_S phải NHỎ HƠN JOBS_API_REQUEST_TIMEOUT_S "
                "(ngân sách timeout phải lồng nhau: client > request > query)"
            )
        return v

    @field_validator("warehouse_backend")
    @classmethod
    def _backend_available(cls, v: str):
        # FAIL-FAST ở boot cho MỌI backend chưa khả dụng (tránh boot-rồi-500 lúc request).
        # Migration mở dần: Phase 0 chỉ 'fake'; 'bigquery' bật ở Phase 3; 'duckdb' khôi phục Phase 4.
        if v in _AVAILABLE_BACKENDS:
            return v
        msg = {
            "duckdb": "warehouse_backend='duckdb' TẠM tắt trong migration — khôi phục ở Phase 4.",
            "bigquery": "warehouse_backend='bigquery' CHƯA khả dụng — sẽ có ở Phase 3.",
        }.get(v, f"warehouse_backend='{v}' không hỗ trợ.")
        raise ValueError(f"{msg} Hiện chỉ dùng được 'fake' (migration Phase 0).")

    @field_validator("page_token_secret")
    @classmethod
    def _refuse_default_secret_outside_local(cls, v: str, info):
        # Không cho phép chạy prod bằng secret mặc định.
        env = (info.data or {}).get("env", "local")
        if env != "local" and v == "dev-only-insecure-secret":
            raise ValueError("JOBS_API_PAGE_TOKEN_SECRET phải được đặt khi env != local")
        return v

    @field_validator("api_keys")
    @classmethod
    def _require_keys_outside_local(cls, v, info):
        # ★ Không cho chạy prod mà không có key nào (nếu không thì mọi request 401 im lặng).
        env = (info.data or {}).get("env", "local")
        if env != "local" and not v:
            raise ValueError("JOBS_API_API_KEYS phải được đặt khi env != local")
        return v


@lru_cache
def get_settings() -> Settings:
    """Cache lại để không đọc env mỗi request. Test có thể gọi .cache_clear()."""
    return Settings()
