"""Fase 6 P2 tests: deep recon capabilities + 5-layer scheduler events."""

from __future__ import annotations

import json

import pytest

from wth_ai.recon import deepscan
from wth_ai.recon import http as rhttp
from wth_ai.recon.tools.builtin import BuiltinContext


def _ctx(**kw) -> BuiltinContext:
    c = BuiltinContext(target="example.com", host="example.com", base_url="https://example.com")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_ct_logs_parses_crtsh(monkeypatch):
    rows = [{"name_value": "a.example.com\n*.b.example.com"}, {"name_value": "evil.com"}]
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(
        url=url, status=200, headers={}, text=json.dumps(rows)))
    items = deepscan.ct_logs(_ctx())
    hosts = {it["host"] for it in items if it["kind"] == "subdomain"}
    assert "a.example.com" in hosts
    assert "b.example.com" in hosts
    assert "evil.com" not in hosts  # out of scope filtered


def test_archive_urls_scope_filter(monkeypatch):
    text = "https://example.com/old\nhttps://other.com/x\nhttps://api.example.com/v1"
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(
        url=url, status=200, headers={}, text=text))
    items = deepscan.archive_urls(_ctx())
    urls = {it["url"] for it in items}
    assert "https://example.com/old" in urls
    assert "https://api.example.com/v1" in urls
    assert "https://other.com/x" not in urls


def test_auth_surface_detects_login():
    ctx = _ctx(urls=["https://example.com/login", "https://example.com/about",
                     "https://example.com/oauth/callback"])
    kinds = {it["url"]: it["endpoint_kind"] for it in deepscan.auth_surface(ctx)}
    assert kinds.get("https://example.com/login") == "auth"
    assert kinds.get("https://example.com/oauth/callback") == "auth"
    assert "https://example.com/about" not in kinds


def test_upload_surface_form_and_urls():
    ctx = _ctx(
        homepage_html='<form enctype="multipart/form-data"><input type="file"></form>',
        urls=["https://example.com/upload", "https://example.com/home"],
    )
    items = deepscan.upload_surface(ctx)
    assert any(it["kind"] == "finding" for it in items)
    assert any(it.get("endpoint_kind") == "upload" for it in items)


def test_websocket_detection():
    ctx = _ctx(js_bodies={"a.js": 'var s=new WebSocket("wss://example.com/ws");'})
    items = deepscan.websocket(ctx)
    assert any(it.get("endpoint_kind") == "websocket" for it in items)
    assert any(it["kind"] == "finding" for it in items)


def test_cdn_waf_from_headers():
    ctx = _ctx(homepage_headers={"CF-Ray": "abc", "Server": "cloudflare"})
    techs = {it["technology"] for it in deepscan.cdn_waf(ctx)}
    assert "cloudflare" in techs


def test_third_party_external_hosts():
    ctx = _ctx(homepage_html='<script src="https://cdn.jsdelivr.net/x.js"></script>'
                             '<img src="https://example.com/logo.png">')
    providers = {(it["provider"], it["asset"]) for it in deepscan.third_party(ctx)}
    assert ("third_party", "cdn.jsdelivr.net") in providers
    assert all(a != "example.com" for _, a in providers)


def test_api_schema_no_false_positive_on_generic_200(monkeypatch):
    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(
        url=url, status=200, headers={}, text="<html>not an api</html>"))
    assert deepscan.api_schema(_ctx()) == []


def test_api_schema_detects_openapi(monkeypatch):
    def fake(url, **k):
        if "openapi.json" in url:
            return rhttp.HttpResponse(url=url, status=200, headers={},
                                      text='{"openapi":"3.0.0","paths":{}}')
        return rhttp.HttpResponse(url=url, status=404, headers={}, text="nope")
    monkeypatch.setattr(rhttp, "fetch", fake)
    items = deepscan.api_schema(_ctx())
    assert any(it.get("endpoint_kind") == "api_schema" for it in items)


def test_run_step_never_raises(monkeypatch):
    def boom(url, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(rhttp, "fetch", boom)
    assert deepscan.run_step("ct_logs", _ctx()) == []  # swallowed


def test_engine_emits_layer_and_step_events(tmp_path, monkeypatch):
    monkeypatch.setenv("WTH_HOME", str(tmp_path))
    from wth_ai.events import EventBus
    from wth_ai.recon import ReconEngine, dns, tls

    monkeypatch.setattr(rhttp, "fetch", lambda url, **k: rhttp.HttpResponse(
        url=rhttp.normalize_url(url), status=200,
        headers={"Content-Type": "text/html", "Server": "nginx"},
        text="<html><body><a href='/login'>x</a></body></html>"))
    monkeypatch.setattr(dns, "resolve_host", lambda h: [{"record_type": "A", "value": "1.2.3.4"}])
    monkeypatch.setattr(tls, "cert_info", lambda h, port=443, timeout=8: {
        "subject": ((("commonName", h),),), "issuer": ((("commonName", "CA"),),),
        "notAfter": "Jan 1 2030", "subjectAltName": (("DNS", h),)})

    bus = EventBus()
    seen: list[str] = []
    bus.subscribe(lambda e: seen.append(e.type))
    ReconEngine().run("example.com", profile="recon-only", job_id="JOB-layers", bus=bus)
    assert "recon.layer_started" in seen
    assert "recon.step_completed" in seen
    # all five layers announced
    layers = {e.data["layer"] for e in bus.history if e.type == "recon.layer_started"}
    assert {"layer1", "layer2", "layer3", "layer4", "layer5"} <= layers
