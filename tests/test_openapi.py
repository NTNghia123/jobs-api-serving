def test_openapi_sinh_duoc(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "Jobs Serving API"


def test_moi_endpoint_co_operation_id(client):
    spec = client.get("/openapi.json").json()
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            assert op.get("operationId"), f"thiếu operationId: {method.upper()} {path}"


def test_enum_seniority_trong_openapi(client):
    spec = client.get("/openapi.json").json()
    sen = spec["components"]["schemas"]["Seniority"]
    assert set(sen["enum"]) == {"junior", "mid", "senior"}
