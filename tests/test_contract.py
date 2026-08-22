"""Test hợp đồng: những thứ consumer được phép tin tưởng.

Đã căn chỉnh theo schema thật: filter là seniority/experience_max/salary_min/country;
không còn posted_after, city, job_function.
"""


def test_search_tra_ve_dung_hinh_dang(client, base_body):
    r = client.post("/v1/jobs/search", json=base_body)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"items", "next_page_token", "total_estimated", "as_of", "request_id"}
    if body["items"]:
        item = body["items"][0]
        assert {"job_id", "title", "company_name", "seniority", "years_exp"} <= set(item)


def test_khong_co_truong_pii_trong_response(client, base_body):
    """Ràng buộc PII: không trả email/mật khẩu/ngày sinh/liên hệ của ai."""
    body = client.post("/v1/jobs/search", json=base_body).json()
    cam = {"email", "password", "dob", "phone", "contactno", "contact",
           "address", "hash", "resume", "candidate", "applicant"}
    for item in body["items"]:
        for key in item:
            assert not any(c in key.lower() for c in cam), f"trường nghi ngờ PII: {key}"


def test_khong_can_field_bat_buoc(client):
    """Không còn posted_after: một body rỗng vẫn hợp lệ."""
    r = client.post("/v1/jobs/search", json={})
    assert r.status_code == 200


def test_filter_la_bi_tu_choi(client, base_body):
    """extra='forbid': gõ sai / gửi field đã bị loại (city) phải báo lỗi."""
    r = client.post("/v1/jobs/search", json={**base_body, "filters": {"city": "HN"}})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_FILTER"


def test_seniority_ngoai_enum_bi_tu_choi(client, base_body):
    r = client.post("/v1/jobs/search", json={**base_body, "filters": {"seniority": "lead+"}})
    assert r.status_code == 400


def test_loc_theo_seniority(client, base_body):
    body = client.post("/v1/jobs/search",
                       json={**base_body, "filters": {"seniority": "senior"}, "limit": 50}).json()
    assert all(i["seniority"] == "senior" for i in body["items"])


def test_loc_theo_country(client, base_body):
    body = client.post("/v1/jobs/search",
                       json={**base_body, "filters": {"country": "Denmark"}, "limit": 50}).json()
    assert all(i["country"] == "Denmark" for i in body["items"])


def test_salary_min_loc_dung(client):
    body = client.post("/v1/jobs/search",
                       json={"filters": {"salary_min": 78000}, "limit": 50}).json()
    assert all(i["salary_max"] >= 78000 for i in body["items"])


def test_limit_vuot_nguong_bi_tu_choi(client, base_body):
    r = client.post("/v1/jobs/search", json={**base_body, "limit": 1000})
    assert r.status_code == 400
    assert r.json()["error"]["field"] == "limit"


def test_moi_loi_deu_cung_mot_envelope(client, base_body):
    r = client.post("/v1/jobs/search", json={**base_body, "filters": {"city": "HN"}})
    err = r.json()["error"]
    assert set(err) == {"code", "message", "field", "request_id"}
