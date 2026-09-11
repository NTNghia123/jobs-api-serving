from app.domain.catalog import FILTER_NAMES


def test_metadata_liet_ke_dung_bo_filter(client):
    r = client.get("/v1/metadata")
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["filters"]}
    assert names == set(FILTER_NAMES)
    assert names == {"posted_after", "posted_before", "source", "seniority",
                     "category", "salary_min", "experience_max"}


def test_metadata_cong_bo_dimensions_windows(client):
    body = client.get("/v1/metadata").json()
    assert set(body["dimensions"]) == {"source", "seniority", "category"}
    assert set(body["windows"]) == {"90d", "all_time"}
    assert body["metrics_min_sample_size"] >= 1


def test_khong_con_filter_da_bi_loai(client):
    names = {f["name"] for f in client.get("/v1/metadata").json()["filters"]}
    for removed in ("city", "country", "job_function", "employment_type", "work_mode"):
        assert removed not in names


def test_metadata_co_as_of(client):
    assert "as_of" in client.get("/v1/metadata").json()
