"""Test an toàn SQL injection — chứng minh tham số hoá THẬT SỰ bảo vệ.

[FILE MỚI]  Đích thật: tests/test_injection_safety.py
Hướng dẫn:  ../W4-Huong-Dan-Thuc-Hien.md §5   (cần DuckDBJobRepository từ Tuần 3)

Đây là test có giá trị nhất của tuần: nạp một giá trị filter ĐỘC HẠI và chứng minh
nó bị coi là DỮ LIỆU (tham số), không phải mã SQL — bảng không bị xoá, không rò dữ liệu.
"""
from __future__ import annotations

import duckdb
import pytest

from app.models.jobs import SearchFilters, SearchRequest
from app.infrastructure.warehouse.duckdb_jobs import DuckDBJobRepository


@pytest.fixture
def duckdb_path(tmp_path) -> str:
    p = tmp_path / "inj.duckdb"
    con = duckdb.connect(str(p))
    con.execute(
        """
        CREATE TABLE silver_jobs (
            job_id INTEGER, title VARCHAR, company_name VARCHAR, country VARCHAR,
            seniority VARCHAR, years_exp INTEGER, salary_min INTEGER,
            salary_max INTEGER, qualification VARCHAR, url VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO silver_jobs VALUES "
        "(1,'A','C1','Denmark','senior',4,39918,78808,'q','/view-job-post.php?id=1')"
    )
    con.close()
    return str(p)


def test_gia_tri_doc_hai_khong_pha_duoc_bang(duckdb_path):
    """country = payload injection cổ điển. Kỳ vọng: 0 dòng + bảng CÒN NGUYÊN."""
    repo = DuckDBJobRepository(duckdb_path)
    payload = "Denmark'; DROP TABLE silver_jobs; --"
    req = SearchRequest(filters=SearchFilters(country=payload), limit=20)

    res = repo.search(req, cursor=None)
    assert res.items == []          # không country nào khớp chuỗi literal đó → 0 dòng

    # Chốt hạ: bảng vẫn còn → DROP KHÔNG hề chạy (payload chỉ là giá trị tham số).
    con = duckdb.connect(duckdb_path, read_only=True)
    n = con.execute("SELECT COUNT(*) FROM silver_jobs").fetchone()[0]
    con.close()
    assert n == 1                   # nếu injection chạy được thì bảng đã bị xoá → lỗi truy vấn


def test_gia_tri_co_dau_nhay_van_loc_dung(duckdb_path):
    """Giá trị có dấu nháy đơn vẫn được xử lý an toàn như dữ liệu thường."""
    repo = DuckDBJobRepository(duckdb_path)
    req = SearchRequest(filters=SearchFilters(country="O'Hare"), limit=20)
    res = repo.search(req, cursor=None)   # không khớp, nhưng KHÔNG được ném lỗi cú pháp SQL
    assert res.items == []
