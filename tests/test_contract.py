"""Test hợp đồng /v1/jobs/search — căn theo contract dữ liệu thật (Mongo → BigQuery).

Filter: posted_after (bắt buộc), posted_before, source, seniority, category, salary_min,
experience_max. JobItem: composite job_id, salary VND/tháng, categories repeated.
"""


def test_search_tra_ve_dung_hinh_dang(client, base_body):
    r = client.post("/v1/jobs/search", json=base_body)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "next_page_token", "total_estimated", "as_of", "request_id"}
    item = body["items"][0]
    assert {"job_id", "source", "title", "seniority", "salary_max_vnd_month",
            "categories", "effective_posted_date"} <= set(item)
    assert ":" in item["job_id"]   # composite '<source>:<external_id>'


def test_khong_co_truong_pii_trong_response(client, base_body):
    body = client.post("/v1/jobs/search", json=base_body).json()
    cam = {"email", "password", "dob", "phone", "contactno", "contact",
           "hash", "resume", "candidate", "applicant", "raw"}
    for item in body["items"]:
        for key in item:
            assert not any(c in key.lower() for c in cam), f"trường nghi ngờ PII: {key}"


def test_posted_after_bat_buoc(client):
    """Thiếu posted_after (filters rỗng / body rỗng) → 400."""
    assert client.post("/v1/jobs/search", json={}).status_code == 400
    assert client.post("/v1/jobs/search", json={"filters": {}}).status_code == 400


def test_filter_la_bi_tu_choi(client, base_body):
    r = client.post("/v1/jobs/search",
                    json={**base_body, "filters": {"posted_after": "2020-01-01", "city": "HN"}})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_FILTER"


def test_seniority_ngoai_enum_bi_tu_choi(client, base_body):
    r = client.post("/v1/jobs/search",
                    json={**base_body, "filters": {"posted_after": "2020-01-01", "seniority": "lead+"}})
    assert r.status_code == 400


def test_loc_theo_seniority(client, base_body):
    body = client.post("/v1/jobs/search",
                       json={"filters": {"posted_after": "2020-01-01", "seniority": "senior"}, "limit": 50}).json()
    assert body["items"] and all(i["seniority"] == "senior" for i in body["items"])


def test_loc_theo_source(client):
    body = client.post("/v1/jobs/search",
                       json={"filters": {"posted_after": "2020-01-01", "source": "vietnamworks"}, "limit": 50}).json()
    assert body["items"] and all(i["source"] == "vietnamworks" for i in body["items"])


def test_loc_theo_category_khong_nhan_dong(client):
    """category filter (EXISTS) — job nhiều category không bị trả trùng."""
    body = client.post("/v1/jobs/search",
                       json={"filters": {"posted_after": "2020-01-01", "category": "topdev:g1~j5"}, "limit": 50}).json()
    ids = [i["job_id"] for i in body["items"]]
    assert ids and len(ids) == len(set(ids))
    assert all(any(c["category_key"] == "topdev:g1~j5" for c in i["categories"]) for i in body["items"])


def test_salary_min_loc_theo_vnd_month(client):
    body = client.post("/v1/jobs/search",
                       json={"filters": {"posted_after": "2020-01-01", "salary_min": 45000000}, "limit": 50}).json()
    assert all(i["salary_max_vnd_month"] >= 45000000 for i in body["items"])


def test_limit_vuot_nguong_bi_tu_choi(client, base_body):
    r = client.post("/v1/jobs/search", json={**base_body, "limit": 1000})
    assert r.status_code == 400
    assert r.json()["error"]["field"] == "limit"


def test_category_multi_khong_nhan_dong(client):
    """job 1001 có 2 category (Backend + Sales): tìm theo mỗi category trả đúng 1 lần, giữ cả 2."""
    a = client.post("/v1/jobs/search",
                    json={"filters": {"posted_after": "2020-01-01", "category": "topdev:g1~j5"}, "limit": 50}).json()["items"]
    b = client.post("/v1/jobs/search",
                    json={"filters": {"posted_after": "2020-01-01", "category": "topdev:g14~j22"}, "limit": 50}).json()["items"]
    assert [i["job_id"] for i in a].count("topdev:1001") == 1
    assert [i["job_id"] for i in b].count("topdev:1001") == 1
    job = next(i for i in a if i["job_id"] == "topdev:1001")
    keys = {c["category_key"] for c in job["categories"]}
    assert {"topdev:g1~j5", "topdev:g14~j22"} <= keys


def test_khoang_ngay_dao_nguoc_bi_tu_choi(client):
    r = client.post("/v1/jobs/search",
                    json={"filters": {"posted_after": "2026-09-01", "posted_before": "2026-08-01"}})
    assert r.status_code == 400
    assert r.json()["error"]["field"] == "filters.posted_before"


def test_moi_loi_deu_cung_mot_envelope(client, base_body):
    r = client.post("/v1/jobs/search",
                    json={**base_body, "filters": {"posted_after": "2020-01-01", "city": "HN"}})
    err = r.json()["error"]
    assert set(err) == {"code", "message", "field", "request_id"}
