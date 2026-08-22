import pytest

from app.domain.pagination import decode_page_token, encode_page_token
from app.errors import InvalidPageTokenError

SECRET = "test-secret"


def test_token_di_va_ve_nguyen_ven():
    tok = encode_page_token({"k": [1, 2]}, fingerprint="abc", secret=SECRET)
    assert decode_page_token(tok, fingerprint="abc", secret=SECRET) == {"k": [1, 2]}


def test_token_bi_sua_thi_bi_tu_choi():
    tok = encode_page_token({"k": [1]}, fingerprint="abc", secret=SECRET)
    payload, sig = tok.split(".")
    forged = payload[:-2] + "AA" + "." + sig
    with pytest.raises(InvalidPageTokenError):
        decode_page_token(forged, fingerprint="abc", secret=SECRET)


def test_token_cua_bo_filter_khac_bi_tu_choi():
    tok = encode_page_token({"k": [1]}, fingerprint="abc", secret=SECRET)
    with pytest.raises(InvalidPageTokenError):
        decode_page_token(tok, fingerprint="xyz", secret=SECRET)


def test_phan_trang_khong_trung_khong_sot(client):
    seen, token = [], None
    for _ in range(20):
        body = {"limit": 3}
        if token:
            body["page_token"] = token
        data = client.post("/v1/jobs/search", json=body).json()
        seen += [i["job_id"] for i in data["items"]]
        token = data["next_page_token"]
        if not token:
            break
    assert len(seen) == len(set(seen)), "có bản ghi bị trùng giữa các trang"
    assert len(seen) == data["total_estimated"], "có bản ghi bị sót"


def test_doi_filter_nhung_giu_token_cu_bi_tu_choi(client):
    first = client.post("/v1/jobs/search", json={"limit": 2}).json()
    r = client.post(
        "/v1/jobs/search",
        json={"limit": 2, "filters": {"seniority": "mid"},
              "page_token": first["next_page_token"]},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_PAGE_TOKEN"
