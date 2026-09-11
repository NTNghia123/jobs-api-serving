"""Test hợp đồng /v1/market/metrics — 3 count, window, k-anonymity, unknown bucket."""


def test_metrics_hinh_dang_va_counts(client):
    r = client.get("/v1/market/metrics?dimension=seniority&window=90d")
    assert r.status_code == 200
    body = r.json()
    assert body["dimension"] == "seniority" and body["window"] == "90d"
    assert body["window_start"] and body["window_end"]      # 90d có mốc cửa sổ
    item = body["items"][0]
    assert {"dimension_value", "posting_count", "salary_disclosed_count",
            "salary_sample_count", "median_salary_vnd_month"} <= set(item)
    # bất biến: sample <= disclosed <= posting
    for it in body["items"]:
        assert it["salary_sample_count"] <= it["salary_disclosed_count"] <= it["posting_count"]


def test_all_time_khong_co_moc_cua_so(client):
    body = client.get("/v1/market/metrics?dimension=source&window=all_time").json()
    assert body["window"] == "all_time"
    assert body["window_start"] is None and body["window_end"] is None


def test_k_anonymity_che_median_theo_sample_count(client):
    min_size = client.get("/v1/metadata").json()["metrics_min_sample_size"]
    body = client.get("/v1/market/metrics?dimension=seniority&window=90d").json()
    unknown = next(i for i in body["items"] if i["dimension_value"] == "unknown")
    assert unknown["salary_sample_count"] < min_size       # mẫu nhỏ
    assert unknown["median_salary_vnd_month"] is None       # → median bị che
    assert unknown["display_name"] == "Unknown"


def test_unknown_category_bucket(client):
    """Job thiếu category xuất hiện dưới '<source>:unknown' — posting không biến mất, k-anon theo sample."""
    min_size = client.get("/v1/metadata").json()["metrics_min_sample_size"]
    body = client.get("/v1/market/metrics?dimension=category&window=all_time").json()
    vals = {i["dimension_value"]: i for i in body["items"]}
    assert "topdev:unknown" in vals
    u = vals["topdev:unknown"]
    assert u["display_name"] == "Unknown" and u["posting_count"] > 0
    assert (u["median_salary_vnd_month"] is None) == (u["salary_sample_count"] < min_size)


def test_dimension_va_window_la_bi_tu_choi(client):
    assert client.get("/v1/market/metrics?dimension=bogus").status_code == 400
    assert client.get("/v1/market/metrics?dimension=source&window=7d").status_code == 400
