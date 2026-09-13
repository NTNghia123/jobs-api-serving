"""Test fail-fast backend + cấu hình BigQuery.

'fake', 'bigquery', 'duckdb' đều khả dụng; giá trị lạ bị chặn ở boot.
backend=bigquery mà thiếu project/dataset → KHÔNG boot.
"""
import pytest

from app.settings import Settings


def test_fake_duoc_chap_nhan():
    assert Settings(warehouse_backend="fake").warehouse_backend == "fake"


def test_duckdb_duoc_chap_nhan():
    # Phase 4: duckdb khôi phục (dev). duckdb_path có default → boot được không cần đặt thêm.
    assert Settings(warehouse_backend="duckdb").warehouse_backend == "duckdb"


def test_bigquery_du_config_duoc_chap_nhan():
    s = Settings(warehouse_backend="bigquery", bq_project="my-proj", bq_dataset="jobs_prod")
    assert s.warehouse_backend == "bigquery"
    assert (s.bq_project, s.bq_dataset) == ("my-proj", "jobs_prod")
    assert s.bq_location == "asia-southeast1"          # mặc định
    assert s.bq_maximum_bytes_billed == 2_000_000_000  # cost guard mặc định


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"warehouse_backend": "bigquery", "bq_dataset": "jobs_prod"}, "BQ_PROJECT"),   # thiếu project
        ({"warehouse_backend": "bigquery", "bq_project": "my-proj"}, "BQ_DATASET"),     # thiếu dataset
        ({"warehouse_backend": "bigquery"}, "BQ_PROJECT"),                              # thiếu cả hai
    ],
)
def test_bigquery_thieu_config_bi_chan_o_boot(kwargs, fragment):
    with pytest.raises(ValueError) as e:
        Settings(**kwargs)
    assert fragment in str(e.value)


def test_fake_khong_can_bq_config():
    # backend=fake KHÔNG yêu cầu bq_* (test/dev chạy sạch không cần GCP).
    assert Settings(warehouse_backend="fake").bq_project == ""


@pytest.mark.parametrize("backend", ["postgres", "mysql", "opensearch"])
def test_backend_la_bi_chan_o_boot(backend):
    with pytest.raises(ValueError) as e:
        Settings(warehouse_backend=backend)
    msg = str(e.value)
    assert "không hỗ trợ" in msg and "fake" in msg   # thông báo rõ + gợi ý backend hợp lệ
