"""Surface discovery dari HTML/JS — tanpa browser, tanpa asumsi framework.

Tujuan: mengubah satu halaman yang diberikan operator menjadi kandidat
`Surface` yang benar-benar ada di halaman itu (form, link same-origin, endpoint
inline-JS). Modul ini murni: tidak mengirim HTTP dan tidak memilih payload.
Pemanggil (tool AI/executor) yang menyediakan hasil fetch dan tetap tunduk pada
scope/permission gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable
from html import unescape
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

from wth_ai.pipeline.models import Surface

try:  # opsional saat runtime minimal; pyyaml sudah dependency proyek
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

_ASSET_EXT = {
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map", ".mp4", ".mp3", ".pdf",
}
_SKIP_INPUT_TYPES = {"submit", "button", "reset", "image", "file"}
_JS_GET_PATTERNS = (
    re.compile(r"""(?:fetch|axios\.get)\s*\(\s*['"`]([^'"`\s]+)['"`]""", re.I),
    re.compile(r"""\.open\s*\(\s*['"`]GET['"`]\s*,\s*['"`]([^'"`]+)['"`]""", re.I),
    re.compile(r"""['"`](/(?:api|rest|graphql|v\d+|internal|admin)[^'"`\s]*)['"`]""", re.I),
)
_JS_POST_PATTERNS = (
    re.compile(r"""axios\.post\s*\(\s*['"`]([^'"`\s]+)['"`]""", re.I),
    re.compile(r"""\.open\s*\(\s*['"`]POST['"`]\s*,\s*['"`]([^'"`]+)['"`]""", re.I),
)
_JS_IMPORT_PATTERNS = (
    re.compile(r"""import\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]"""),
    re.compile(r"""import\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
    re.compile(r"""importScripts\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
)
_NON_JS_EXT = {
    ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".mp4", ".mp3", ".pdf", ".json",
}


@dataclass(frozen=True)
class DiscoveredSurface:
    surface: Surface
    source: str  # form | link | js
    label: str = ""
    score: int = 0

    def to_dict(self) -> dict:
        data = self.surface.to_dict()
        data.update({"source": self.source, "label": self.label, "score": self.score})
        return data


@dataclass
class _Form:
    action: str = ""
    method: str = "GET"
    enctype: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)
    select_name: str | None = None
    select_has_value: bool = False
    textarea_name: str | None = None
    textarea_value: list[str] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.forms: list[tuple[str, str, str, list[tuple[str, str]]]] = []
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.external_scripts: list[str] = []
        self._form: _Form | None = None
        self._in_script = False
        self._script_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k.lower(): (v if v is not None else "") for k, v in attrs}
        tag = tag.lower()
        if tag == "base" and data.get("href"):
            self.base_url = urljoin(self.base_url, data["href"])
        elif tag == "form":
            self._form = _Form(
                action=data.get("action", ""),
                method=(data.get("method", "get") or "get").upper(),
                enctype=(data.get("enctype", "") or "").lower(),
            )
        elif tag == "input" and self._form is not None:
            name = data.get("name", "").strip()
            if not name:
                return
            itype = (data.get("type", "text") or "text").lower()
            if itype in _SKIP_INPUT_TYPES:
                return
            if itype in {"checkbox", "radio"} and "checked" not in data:
                return
            value = data.get("value", "")
            if itype in {"checkbox", "radio"} and value == "":
                value = "on"
            self._form.fields.append((name, value))
        elif tag == "select" and self._form is not None:
            self._form.select_name = data.get("name", "").strip() or None
            self._form.select_has_value = False
        elif tag == "option" and self._form is not None and self._form.select_name:
            value = data.get("value")
            if value is None:
                value = ""
            if not self._form.select_has_value or "selected" in data:
                existing = [(n, v) for n, v in self._form.fields if n != self._form.select_name]
                existing.append((self._form.select_name, value))
                self._form.fields = existing
                self._form.select_has_value = True
        elif tag == "textarea" and self._form is not None:
            self._form.textarea_name = data.get("name", "").strip() or None
            self._form.textarea_value = []
        elif tag == "a" and data.get("href"):
            self.links.append(data["href"])
        elif tag == "script":
            if data.get("src"):
                # Fetch tetap keputusan caller (butuh request teraudit); parser
                # hanya melaporkan URL same-origin untuk diambil nanti.
                self.external_scripts.append(urljoin(self.base_url, data["src"]))
                return
            self._in_script = True
            self._script_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buf.append(data)
        if self._form is not None and self._form.textarea_name:
            self._form.textarea_value.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "form" and self._form is not None:
            form = self._form
            if form.textarea_name and form.textarea_value:
                form.fields.append((form.textarea_name, "".join(form.textarea_value).strip()))
            self.forms.append((form.action, form.method, form.enctype, list(form.fields)))
            self._form = None
        elif tag == "textarea" and self._form is not None:
            if self._form.textarea_name and self._form.textarea_value:
                self._form.fields.append(
                    (self._form.textarea_name, "".join(self._form.textarea_value).strip())
                )
            self._form.textarea_name = None
            self._form.textarea_value = []
        elif tag == "script" and self._in_script:
            self.scripts.append("".join(self._script_buf))
            self._in_script = False
            self._script_buf = []


def _same_origin(url: str, base_url: str) -> bool:
    left, right = urlsplit(url), urlsplit(base_url)
    return left.scheme in {"http", "https"} and (left.scheme, left.netloc) == (right.scheme, right.netloc)


def _is_asset(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return any(path.endswith(ext) for ext in _ASSET_EXT)


def _surface_from_form(base_url: str, action: str, method: str, enctype: str,
                       fields: list[tuple[str, str]]) -> Surface | None:
    if not fields or enctype == "multipart/form-data":
        return None
    target = urljoin(base_url, action or base_url)
    if not _same_origin(target, base_url):
        return None
    method = method.upper()
    if method == "GET":
        parts = urlsplit(target)
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        pairs.extend((name, value) for name, value in fields)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs, doseq=True), ""))
        return Surface.from_target(url, method="GET")
    body = urlencode(fields, doseq=True)
    return Surface.from_target(
        target, method=method,
        headers={"Content-Type": "application/x-www-form-urlencoded"}, body=body,
    )


def _dedupe(items: Iterable[DiscoveredSurface]) -> list[DiscoveredSurface]:
    out: list[DiscoveredSurface] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for item in items:
        key = (
            item.surface.method.upper(),
            item.surface.url,
            tuple(sorted(p.name for p in item.surface.params)),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def discover_from_js(base_url: str, script: str, *, max_surfaces: int = 25) -> list[DiscoveredSurface]:
    """Ekstrak endpoint dari isi JavaScript mentah (inline maupun file eksternal)."""
    found: list[DiscoveredSurface] = []
    for patterns, method in ((_JS_GET_PATTERNS, "GET"), (_JS_POST_PATTERNS, "POST")):
        for pattern in patterns:
            for match in pattern.finditer(script or ""):
                target = urljoin(base_url, match.group(1))
                if not _same_origin(target, base_url) or _is_asset(target):
                    continue
                surface = Surface.from_target(target, method=method)
                found.append(DiscoveredSurface(
                    surface, "js", f"js {urlsplit(target).path or '/'}",
                    score=(7 if surface.params else 2) + len(surface.params),
                ))
    return sorted(_dedupe(found), key=lambda item: (-item.score, item.surface.url))[:max_surfaces]


def js_import_urls(base_url: str, script: str, *, max_urls: int = 10) -> list[str]:
    """URL import/chunk same-origin dari JS (statis, dinamis, require, worker).

    Hanya path relatif/absolut yang bisa di-resolve ke origin yang sama. Bare
    specifier (`lodash`, `node:fs`) sengaja diabaikan karena bukan URL yang
    bisa kita fetch tanpa resolver/bundler.
    """
    out: list[str] = []
    seen: set[str] = set()
    for pattern in _JS_IMPORT_PATTERNS:
        for match in pattern.finditer(script or ""):
            raw = match.group(1).strip()
            if not raw or raw.startswith(("data:", "blob:", "node:", "http://", "https://")):
                continue
            if not raw.startswith(("/", "./", "../")):
                continue
            target = urljoin(base_url, raw).split("#", 1)[0]
            if not _same_origin(target, base_url):
                continue
            path = urlsplit(target).path.lower()
            if any(path.endswith(ext) for ext in _NON_JS_EXT):
                continue
            if target in seen:
                continue
            seen.add(target)
            out.append(target)
            if len(out) >= max_urls:
                return out
    return out


def external_script_urls(base_url: str, html: str, *, max_urls: int = 10) -> list[str]:
    """URL <script src> same-origin yang layak difetch caller (tanpa request)."""
    parser = _PageParser(base_url)
    parser.feed(html or "")
    parser.close()
    out: list[str] = []
    seen: set[str] = set()
    for raw in parser.external_scripts:
        target = urljoin(parser.base_url, raw).split("#", 1)[0]
        if not _same_origin(target, parser.base_url) or target in seen:
            continue
        seen.add(target)
        out.append(target)
        if len(out) >= max_urls:
            break
    return out


def discover_from_html(base_url: str, html: str, *, max_surfaces: int = 25) -> list[DiscoveredSurface]:
    """Ekstrak form, link same-origin, dan endpoint inline-JS dari satu halaman."""
    parser = _PageParser(base_url)
    parser.feed(html or "")
    parser.close()

    base_url = parser.base_url
    found: list[DiscoveredSurface] = []
    for action, method, enctype, fields in parser.forms:
        surface = _surface_from_form(base_url, action, method, enctype, fields)
        if surface is not None:
            label = f"{method} form {urlsplit(surface.url).path or '/'}"
            found.append(DiscoveredSurface(surface, "form", label, score=10 + len(surface.params)))

    for href in parser.links:
        target = urljoin(base_url, href)
        if not _same_origin(target, base_url) or _is_asset(target):
            continue
        target = target.split("#", 1)[0]
        surface = Surface.from_target(target)
        if surface.params:
            found.append(DiscoveredSurface(surface, "link", f"link {urlsplit(target).path or '/'}",
                                           score=4 + len(surface.params)))
        else:
            found.append(DiscoveredSurface(surface, "link", f"link {urlsplit(target).path or '/'}", score=1))

    for script in parser.scripts:
        found.extend(discover_from_js(base_url, script, max_surfaces=max_surfaces))

    ordered = sorted(_dedupe(found), key=lambda item: (-item.score, item.surface.url))
    return ordered[:max_surfaces]



def _resolve_refs(node: object, root: dict, *, seen: tuple[str, ...] = (), depth: int = 0) -> object:
    """Resolve `$ref` lokal (`#/components/...`) tanpa fetch eksternal.

    External refs sengaja tidak diambil: discovery tidak boleh membuat request
    di luar scope hanya karena dokumen menyebut URL. Siklus dipotong, kedalaman
    dibatasi, dan sibling `$ref` tetap dipertahankan.
    """
    if depth > 12:
        return node
    if isinstance(node, list):
        return [_resolve_refs(item, root, seen=seen, depth=depth + 1) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in seen:
            return {}
        target: object = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                target = {}
                break
            target = target[part]
        resolved = _resolve_refs(target, root, seen=(*seen, ref), depth=depth + 1)
        if isinstance(resolved, dict):
            merged = dict(resolved)
            merged.update({k: _resolve_refs(v, root, seen=seen, depth=depth + 1)
                           for k, v in node.items() if k != "$ref"})
            return merged
        return resolved
    return {key: _resolve_refs(value, root, seen=seen, depth=depth + 1)
            for key, value in node.items()}


def _sample(schema: dict | None, *, depth: int = 0) -> object:
    if not isinstance(schema, dict) or depth > 3:
        return "test"
    for key in ("example", "default"):
        if key in schema:
            return schema[key]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]
    stype = schema.get("type")
    if stype == "object" or "properties" in schema:
        props = schema.get("properties") or {}
        return {name: _sample(prop, depth=depth + 1) for name, prop in props.items() if isinstance(prop, dict)}
    if stype == "array":
        return [_sample(schema.get("items"), depth=depth + 1)]
    if stype == "integer":
        return 1
    if stype == "number":
        return 1.5
    if stype == "boolean":
        return True
    return "test"


def _openapi_body(operation: dict) -> tuple[str, dict[str, str]]:
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content") or {}
    for media, spec in content.items():
        if not isinstance(spec, dict):
            continue
        schema = spec.get("schema") or {}
        example = spec.get("example", _sample(schema))
        if "json" in media:
            return json.dumps(example), {"Content-Type": "application/json"}
        if "x-www-form-urlencoded" in media:
            if isinstance(example, dict):
                return urlencode([(str(k), str(v)) for k, v in example.items()]), {
                    "Content-Type": "application/x-www-form-urlencoded",
                }
    return "", {}


def discover_from_openapi(base_url: str, payload: str, *, max_surfaces: int = 25) -> list[DiscoveredSurface]:
    """Kandidat Surface dari dokumen OpenAPI/Swagger JSON (subset aman)."""
    try:
        spec = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        if yaml is None:
            return []
        try:
            spec = yaml.safe_load(payload)
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(spec, dict):
        return []
    spec = _resolve_refs(spec, spec)
    if not isinstance(spec, dict):
        return []
    servers = spec.get("servers") or []
    server_url = base_url
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        server_url = urljoin(base_url, str(servers[0].get("url") or base_url))
    elif spec.get("host"):
        scheme = (spec.get("schemes") or ["https"])[0]
        server_url = f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    server_url = server_url.rstrip("/") + "/"
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        return []

    found: list[DiscoveredSurface] = []
    methods = ("get", "post", "put", "patch", "delete", "head", "options")
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method in methods:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue
            path_value = str(path)
            query: list[tuple[str, str]] = []
            params = []
            for source in (item.get("parameters"), operation.get("parameters")):
                if isinstance(source, list):
                    params.extend(p for p in source if isinstance(p, dict))
            for param in params:
                name = str(param.get("name") or "").strip()
                if not name:
                    continue
                value = _sample(param.get("schema") or param)
                if param.get("in") == "path":
                    path_value = path_value.replace("{" + name + "}", quote(str(value), safe=""))
                elif param.get("in") == "query":
                    query.append((name, str(value)))
            path_value = re.sub(r"\{[^}]+\}", "1", path_value)
            target = server_url + path_value.lstrip("/")
            if not _same_origin(target, base_url):
                continue
            if query:
                target += ("&" if "?" in target else "?") + urlencode(query, doseq=True)
            body, headers = _openapi_body(operation)
            if not body and method in {"post", "put", "patch"}:
                # Tanpa skema body, tetap kirim body kosong; jangan mengarang.
                headers = {"Content-Type": "application/json"}
                body = "{}"
            surface = Surface.from_target(target, method=method.upper(), headers=headers, body=body)
            label = f"{method.upper()} {path}"
            found.append(DiscoveredSurface(
                surface, "openapi", label,
                score=9 + len(surface.params) + (3 if body else 0),
            ))
    return sorted(_dedupe(found), key=lambda item: (-item.score, item.surface.url))[:max_surfaces]


def discover_from_sitemap(base_url: str, payload: str, *, max_surfaces: int = 50) -> list[DiscoveredSurface]:
    """Kandidat Surface dari sitemap XML (tanpa parser XML rapuh)."""
    found: list[DiscoveredSurface] = []
    for match in re.finditer(r"<loc>\s*(.*?)\s*</loc>", payload or "", re.I | re.S):
        target = urljoin(base_url, unescape(match.group(1)).strip())
        if not _same_origin(target, base_url) or _is_asset(target):
            continue
        surface = Surface.from_target(target)
        found.append(DiscoveredSurface(
            surface, "sitemap", f"sitemap {urlsplit(target).path or '/'}",
            score=(3 if surface.params else 1) + len(surface.params),
        ))
    return sorted(_dedupe(found), key=lambda item: (-item.score, item.surface.url))[:max_surfaces]


def discover_document(base_url: str, body: str, content_type: str = "", *, max_surfaces: int = 25) -> list[DiscoveredSurface]:
    """Pilih parser berdasarkan isi dokumen seed, bukan asumsi HTML."""
    text = (body or "").lstrip()
    ctype = (content_type or "").lower()
    if "json" in ctype or text.startswith("{"):
        head = text[:4000]
        if "openapi" in head.lower() or "swagger" in head.lower():
            return discover_from_openapi(base_url, text, max_surfaces=max_surfaces)
    if (
        "yaml" in ctype or "yml" in ctype
        or re.match(r"^\s*(?:---\s*)?(?:openapi|swagger)\s*:", text, re.I)
    ):
        head = text[:4000]
        if "openapi" in head.lower() or "swagger" in head.lower():
            return discover_from_openapi(base_url, text, max_surfaces=max_surfaces)
    if text.startswith("<") and ("<urlset" in text.lower() or "<sitemapindex" in text.lower()):
        return discover_from_sitemap(base_url, text, max_surfaces=max_surfaces)
    return discover_from_html(base_url, text, max_surfaces=max_surfaces)
