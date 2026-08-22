def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "jobs-serving-api"


def test_health_tra_ve_request_id_header(client):
    r = client.get("/health")
    assert r.headers["X-Request-ID"].startswith("req_")


def test_request_id_cua_client_duoc_ton_trong(client):
    r = client.get("/health", headers={"X-Request-ID": "req_from_caller"})
    assert r.headers["X-Request-ID"] == "req_from_caller"
