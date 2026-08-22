from app.domain.catalog import FILTER_NAMES


def test_metadata_liet_ke_dung_bo_filter(client):
    r = client.get("/v1/metadata")
    assert r.status_code == 200
    names = {f["name"] for f in r.json()["filters"]}
    assert names == set(FILTER_NAMES)
    assert names == {"seniority", "experience_max", "salary_min", "country"}


def test_khong_con_filter_da_bi_loai(client):
    names = {f["name"] for f in client.get("/v1/metadata").json()["filters"]}
    for removed in ("city", "job_function", "employment_type", "work_mode", "posted_after"):
        assert removed not in names


def test_metadata_co_as_of(client):
    assert "as_of" in client.get("/v1/metadata").json()
