"""OpenAPI / Postman sebagai target kelas satu."""

import json

import pytest

from wth_ai.targets.spec import KIND_OPENAPI, KIND_POSTMAN, load_spec

OPENAPI = {
    "openapi": "3.0.3",
    "info": {"title": "Orders", "version": "1"},
    "servers": [{"url": "http://172.28.0.10:8080/api"}],
    "paths": {
        "/orders": {"get": {"summary": "list"}, "post": {"summary": "create"}},
        "/orders/{id}": {"get": {"summary": "detail"},
                         "parameters": [{"name": "id", "in": "path"}]},
    },
}

POSTMAN = {
    "info": {"name": "Lab", "schema": "https://schema.getpostman.com/1"},
    "item": [
        {"name": "orders", "item": [
            {"name": "list", "request": {"method": "GET",
                                         "url": {"raw": "http://172.28.0.10/api/orders"}}},
            {"name": "detail", "request": {"method": "GET",
                                           "url": {"raw": "http://172.28.0.10/api/orders/1"}}},
        ]},
        {"name": "login", "request": {"method": "POST",
                                      "url": {"raw": "http://172.28.0.10/api/login"}}},
    ],
}


def _write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_openapi_endpoints_and_hosts(tmp_path):
    spec = load_spec(_write(tmp_path, "openapi.json", OPENAPI))
    assert spec.kind == KIND_OPENAPI
    assert "GET /orders" in spec.endpoints
    assert "POST /orders" in spec.endpoints
    assert spec.hosts() == ["172.28.0.10"]


def test_openapi_parameters_key_is_not_an_endpoint(tmp_path):
    spec = load_spec(_write(tmp_path, "openapi.json", OPENAPI))
    assert not any(endpoint.startswith("PARAMETERS") for endpoint in spec.endpoints)


def test_postman_endpoints_nested(tmp_path):
    spec = load_spec(_write(tmp_path, "postman.json", POSTMAN))
    assert spec.kind == KIND_POSTMAN
    assert len(spec.endpoints) == 3
    assert "POST http://172.28.0.10/api/login" in spec.endpoints
    assert spec.hosts() == ["172.28.0.10"]


def test_unknown_file_is_rejected(tmp_path):
    path = tmp_path / "bukan.json"
    path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_spec(path)


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_spec(tmp_path / "hilang.json")