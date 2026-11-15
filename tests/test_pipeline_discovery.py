"""Surface discovery: form, link same-origin, dan endpoint inline-JS."""

from __future__ import annotations

from wth_ai.pipeline.discovery import (
    discover_document,
    discover_from_html,
    discover_from_js,
    discover_from_openapi,
    discover_from_sitemap,
    external_script_urls,
    js_import_urls,
)


HTML = """
<html><body>
<form action="/search" method="get">
  <input name="q" value="hello">
  <input type="submit" name="go" value="1">
</form>
<form action="/login" method="post">
  <input name="user" value="admin">
  <input name="pass" type="password" value="secret">
  <select name="role"><option value="user">user</option><option value="admin" selected>admin</option></select>
  <input type="checkbox" name="remember" checked>
</form>
<a href="/api/user?id=7">user</a>
<a href="https://evil.test/out?id=1">external</a>
<a href="/static/app.js">asset</a>
<script>
  fetch('/api/items?sort=asc');
  axios.post('/api/login', {});
  const x = "/admin/export?actor=admin";
</script>
</body></html>
"""


def test_forms_links_and_inline_js_are_discovered():
    found = discover_from_html("http://target.test/page", HTML)
    by_key = {(item.surface.method, item.surface.url): item for item in found}

    search = by_key[("GET", "http://target.test/search?q=hello")]
    assert search.source == "form"
    assert [p.name for p in search.surface.params] == ["q"]
    assert "go" not in [p.name for p in search.surface.params], "submit input bukan parameter uji"

    login = by_key[("POST", "http://target.test/login")]
    assert login.source == "form"
    assert {p.name: p.value for p in login.surface.params} == {
        "user": "admin", "pass": "secret", "role": "admin", "remember": "on",
    }
    assert "application/x-www-form-urlencoded" in login.surface.headers["Content-Type"]

    assert ("GET", "http://target.test/api/user?id=7") in by_key
    assert ("GET", "http://target.test/api/items?sort=asc") in by_key
    assert ("POST", "http://target.test/api/login") in by_key
    assert ("GET", "http://target.test/admin/export?actor=admin") in by_key


def test_external_and_asset_links_are_dropped():
    urls = [item.surface.url for item in discover_from_html("http://target.test/page", HTML)]
    assert not any("evil.test" in url for url in urls)
    assert not any(url.endswith(".js") for url in urls)


def test_forms_are_ranked_before_links_and_deduped():
    found = discover_from_html("http://target.test/page", HTML)
    assert found[0].source == "form"
    keys = [(item.surface.method, item.surface.url) for item in found]
    assert len(keys) == len(set(keys)), "surface duplikat harus dibuang"


def test_base_href_is_respected_and_limit_enforced():
    html = '<base href="http://target.test/app/"><a href="detail?id=1">d</a>'
    found = discover_from_html("http://target.test/page", html, max_surfaces=1)
    assert len(found) == 1
    assert found[0].surface.url == "http://target.test/app/detail?id=1"


def test_openapi_paths_and_json_body_are_materialized():
    spec = """
    {"openapi": "3.0.0", "servers": [{"url": "/v1"}],
     "paths": {
       "/users/{id}": {"get": {"parameters": [
         {"name": "id", "in": "path", "schema": {"type": "integer"}},
         {"name": "expand", "in": "query", "schema": {"type": "string", "default": "roles"}}
       ]}},
       "/login": {"post": {"requestBody": {"content": {"application/json": {
         "schema": {"type": "object", "properties": {"user": {"type": "string"}, "pass": {"type": "string"}}}
       }}}}}
     }}
    """
    found = discover_from_openapi("http://target.test/openapi.json", spec)
    by_key = {(x.surface.method, x.surface.url): x.surface for x in found}
    assert by_key[("GET", "http://target.test/v1/users/1?expand=roles")]
    login = by_key[("POST", "http://target.test/v1/login")]
    assert {p.name for p in login.params} == {"user", "pass"}


def test_sitemap_and_document_dispatch():
    sitemap = "<urlset><url><loc>http://target.test/a?id=1</loc></url><url><loc>http://evil.test/x</loc></url></urlset>"
    found = discover_from_sitemap("http://target.test/sitemap.xml", sitemap)
    assert [x.surface.url for x in found] == ["http://target.test/a?id=1"]
    assert discover_document("http://target.test/openapi.json", '{"openapi":"3.0.0","paths":{}}', "application/json") == []
    assert discover_document("http://target.test/x", '<a href="/y?id=1">y</a>', "text/html")


def test_raw_js_and_external_script_urls_are_discovered():
    js = """
    fetch('/api/external?q=1');
    axios.post('/api/save', {x: 1});
    new XMLHttpRequest().open('GET', '/rest/items?id=2');
    """
    found = discover_from_js("http://target.test/app.js", js)
    keys = {(x.surface.method, x.surface.url) for x in found}
    assert ("GET", "http://target.test/api/external?q=1") in keys
    assert ("POST", "http://target.test/api/save") in keys
    assert ("GET", "http://target.test/rest/items?id=2") in keys

    html = '<script src="/static/app.js"></script><script src="https://evil.test/x.js"></script>'
    assert external_script_urls("http://target.test/", html) == ["http://target.test/static/app.js"]


def test_openapi_yaml_is_parsed_and_dispatched():
    spec = """
openapi: 3.0.0
servers:
  - url: /v2
paths:
  /users/{id}:
    get:
      parameters:
        - name: id
          in: path
          schema: {type: integer}
        - name: expand
          in: query
          schema: {type: string, default: roles}
  /login:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                user: {type: string}
                pass: {type: string}
"""
    found = discover_from_openapi("http://target.test/openapi.yaml", spec)
    keys = {(x.surface.method, x.surface.url) for x in found}
    assert ("GET", "http://target.test/v2/users/1?expand=roles") in keys
    assert ("POST", "http://target.test/v2/login") in keys
    assert discover_document(
        "http://target.test/openapi.yaml", spec, "application/yaml",
    )


def test_openapi_local_refs_are_resolved_without_external_fetch():
    spec = """
openapi: 3.0.0
servers: [{url: /v3}]
components:
  parameters:
    UserId:
      name: id
      in: path
      schema: {type: integer}
  schemas:
    Login:
      type: object
      properties:
        user: {type: string}
        pass: {type: string}
paths:
  /users/{id}:
    get:
      parameters:
        - $ref: '#/components/parameters/UserId'
  /login:
    post:
      requestBody:
        content:
          application/json:
            schema: {$ref: '#/components/schemas/Login'}
"""
    found = discover_from_openapi("http://target.test/openapi.yaml", spec)
    by_key = {(x.surface.method, x.surface.url): x.surface for x in found}
    assert ("GET", "http://target.test/v3/users/1") in by_key
    login = by_key[("POST", "http://target.test/v3/login")]
    assert {p.name for p in login.params} == {"user", "pass"}


def test_js_import_graph_is_extracted_same_origin_only():
    js = """
    import x from './chunk.js';
    import('/assets/lazy.mjs');
    require('../legacy.js');
    importScripts('/worker.js');
    import 'lodash';
    import 'https://evil.test/x.js';
    import './style.css';
    """
    urls = js_import_urls("http://target.test/app/main.js", js)
    assert urls == [
        "http://target.test/app/chunk.js",
        "http://target.test/assets/lazy.mjs",
        "http://target.test/legacy.js",
        "http://target.test/worker.js",
    ]
