"""Test fail-fast backend: Phase 0 chỉ 'fake'; các backend khác từ chối rõ ở boot."""
import pytest

from app.settings import Settings


def test_fake_duoc_chap_nhan():
    assert Settings(warehouse_backend="fake").warehouse_backend == "fake"


@pytest.mark.parametrize(
    "backend, fragment",
    [
        ("duckdb", "Phase 4"),      # tạm tắt
        ("bigquery", "Phase 3"),    # chưa khả dụng
        ("postgres", "không hỗ trợ"),  # giá trị lạ
    ],
)
def test_backend_chua_kha_dung_bi_chan_o_boot(backend, fragment):
    with pytest.raises(ValueError) as e:
        Settings(warehouse_backend=backend)
    msg = str(e.value)
    assert fragment in msg and "fake" in msg   # thông báo rõ + gợi ý fake
