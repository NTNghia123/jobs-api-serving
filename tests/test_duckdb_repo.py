"""Test tích hợp cho DuckDBJobRepository.

[FILE MỚI]  Đích thật: tests/test_duckdb_repo.py
Hướng dẫn:  ../W3-Huong-Dan-Thuc-Hien.md §6.2

GIẢI THÍCH vì sao cần lớp test này BÊN CẠNH test hợp đồng (chạy trên fake):
  - Test hợp đồng (fake) đảm bảo HÌNH DẠNG API đúng — nhanh, không cần MySQL/DuckDB.
  - Test tích hợp (đây) đảm bảo SQL THẬT hành xử đúng: filter, sort, NULL, và
    silver_jobs không chứa cột PII. Báo cáo Tuần 2 đã liệt kê đây là 'nợ' cần trả.

Test tự dựng một file DuckDB tạm (tmp_path của pytest) với vài dòng mẫu, KHÔNG cần
MySQL — nên chạy được ở mọi máy.
"""
from __future__ import annotations

import duckdb
import pytest

from app.infrastructure.warehouse.duckdb_jobs import DuckDBJobRepository
from app.models.enums import Seniority, SortOption
from app.models.jobs import SearchFilters, SearchRequest

# Cột an toàn của silver_jobs — KHÔNG được xuất hiện cột PII nào ngoài danh sách này.
_SAFE_COLUMNS = {
    "job_id", "title", "company_name", "country", "seniority",
    "years_exp", "salary_min", "salary_max", "qualification", "url",
}
_PII_FORBIDDEN = {"email", "password", "contactno", "dob", "address"}


@pytest.fixture
def duckdb_path(tmp_path) -> str:
    """Dựng một kho DuckDB tạm với 3 tin mẫu (giống hình dạng silver_jobs thật)."""
    p = tmp_path / "test_analytics.duckdb"
    con = duckdb.connect(str(p))                 # mở GHI để tạo bảng
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
        """
        INSERT INTO silver_jobs VALUES
        (1,'A','C1','Denmark','senior',4,39918,78808,'q','/view-job-post.php?id=1'),
        (2,'B','C2','Bulgaria','mid',3,43334,76458,'q','/view-job-post.php?id=2'),
        (3,'C','C3','Denmark','junior',1,28080,62841,'q','/view-job-post.php?id=3')
        """
    )
    con.close()                                  # nhả khoá để repo mở read_only
    return str(p)


def _req(**filters) -> SearchRequest:
    return SearchRequest(filters=SearchFilters(**filters), limit=20)


def test_filter_country(duckdb_path):
    repo = DuckDBJobRepository(duckdb_path)
    res = repo.search(_req(country="Denmark"), cursor=None)
    ids = {i.job_id for i in res.items}
    assert ids == {1, 3}                          # chỉ tin ở Denmark


def test_filter_seniority(duckdb_path):
    repo = DuckDBJobRepository(duckdb_path)
    res = repo.search(_req(seniority=Seniority.SENIOR), cursor=None)
    assert [i.job_id for i in res.items] == [1]


def test_filter_salary_min_dung_ngu_nghia_salary_max(duckdb_path):
    # salary_min=70000 → lấy tin có salary_max >= 70000 → job 1 (78808) & job 2 (76458)
    repo = DuckDBJobRepository(duckdb_path)
    res = repo.search(_req(salary_min=70000), cursor=None)
    assert {i.job_id for i in res.items} == {1, 2}


def test_sort_salary_max_desc(duckdb_path):
    repo = DuckDBJobRepository(duckdb_path)
    req = SearchRequest(sort=SortOption.SALARY_MAX_DESC, limit=20)
    res = repo.search(req, cursor=None)
    salaries = [i.salary_max for i in res.items]
    assert salaries == sorted(salaries, reverse=True)   # giảm dần


def test_khong_co_cot_pii_trong_silver(duckdb_path):
    # Quét schema bảng silver_jobs: không được có cột PII nào.
    # Dùng information_schema.columns cho chắc (trả thẳng tên cột).
    con = duckdb.connect(duckdb_path, read_only=True)
    cols = {
        row[0].lower()
        for row in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'silver_jobs'"
        ).fetchall()
    }
    con.close()
    assert cols <= _SAFE_COLUMNS
    assert cols.isdisjoint(_PII_FORBIDDEN)


def test_phan_trang_khong_trung_khong_sot(duckdb_path):
    # Duyệt hết bằng limit=1, gom job_id — phải đủ 3, không trùng.
    repo = DuckDBJobRepository(duckdb_path)
    seen: list[int] = []
    cursor = None
    for _ in range(10):                           # trần an toàn tránh vòng lặp vô hạn
        req = SearchRequest(sort=SortOption.SALARY_MAX_DESC, limit=1, filters=SearchFilters())
        res = repo.search(req, cursor=cursor)
        seen += [i.job_id for i in res.items]
        if res.next_cursor is None:
            break
        cursor = res.next_cursor
    assert sorted(seen) == [1, 2, 3]              # đủ, không sót
    assert len(seen) == len(set(seen))            # không trùng
