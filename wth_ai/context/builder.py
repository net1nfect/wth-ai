"""`ContextBuilder` — unified context-block assembly (WU-111, ADR-004).

ADR-004 rejects one monolithic `MemoryService` hiding all 5 memory
mechanisms (`ActionMemory`, `EngagementMemory`, `SessionFtsIndex`,
`HuntMemoryLearning`, `MdContext`) behind a single API -- lifecycles genuinely
differ (session-ephemeral vs cross-session-statistical vs cross-session-
lexical vs human-authored). Instead: each mechanism stays independent, but
exposes/consumes a thin shared contract here (`SOURCE_REGISTRY` names ->
resolver), so both context-building call sites can select a DIFFERENT subset
of the SAME sources rather than each writing its own bespoke assembly logic
that silently drifts (the actual bug found during Fase B research: two call
sites invoked with equal frequency, unequal quality, from being built at
different times).

Scope decision (found during implementation, not in the original ADR/
INTERFACES.md sketch): only `SupervisorAgent._build_context_block()` is
migrated to use this class in WU-111. `WTHOrchestrator._assess_loop()` is
NOT migrated -- it doesn't build a single context STRING at all; it passes
5+ separate named pieces (`resume_context`, `skill_context`,
`learning_context`, `graph_context`, `pre_hypotheses`, `chain_suggestions`)
as distinct keyword arguments directly into `PlannerAgent.plan()`, a
structurally different calling convention. Unifying that would require also
changing `PlannerAgent.plan()`'s signature and internal prompt assembly --
a materially bigger, riskier change than this WU's scope (deferred, not
silently dropped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SOURCE_NAMES: tuple[str, ...] = (
    "action_memory", "md_context", "engagement_graph", "learning", "skill_fusion", "fts_recall",
    "jobs",
)

#: Prioritas default per sumber. Angka lebih besar = dipertahankan lebih dulu
#: saat budget menipis. Sumber "invarian" (scope/permission/phase) BUKAN di
#: sini -- invarian dibangun terpisah dan tidak pernah dipotong.
SOURCE_PRIORITY: dict[str, int] = {
    "jobs": 70,
    "engagement_graph": 60,
    "learning": 50,
    "action_memory": 40,
    "md_context": 30,
    "skill_fusion": 20,
    "fts_recall": 10,
}

_TRUNCATION_MARKER = "\n…[truncated {dropped} chars]…\n"


class BudgetExceededError(ValueError):
    """Invarian keamanan tidak muat dalam anggaran yang diminta.

    Dilempar (bukan dipotong) karena memotong scope/permission secara diam-diam
    adalah regresi keamanan. Pemanggil harus memilih `overflow="keep_invariant"`
    bila memang bersedia melampaui anggaran body demi menjaga invarian.
    """

    def __init__(self, *, invariant_len: int, budget: int) -> None:
        self.invariant_len = invariant_len
        self.budget = budget
        super().__init__(
            f"invariant block ({invariant_len} chars) melebihi budget body ({budget} chars); "
            "tidak dipotong diam-diam. Gunakan overflow='keep_invariant' bila invarian harus utuh."
        )


def normalize_budget(value: Any) -> int:
    """Normalisasi anggaran karakter secara eksplisit.

    Kontrak:
    * `None` -> 0 (unbounded).
    * integer/float/string numerik: nilai <= 0 -> 0 (unbounded, kompatibel
      dengan perilaku lama); nilai > 0 -> int positif.
    * input yang bukan angka -> `ValueError` (bukan `TypeError` mentah dari
      operator perbandingan).

    Catatan: ini CHAR budget, bukan TOKEN budget. Pemetaan token model ada di
    lapisan pemanggil; tidak ada rasio implisit di sini.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        raise ValueError(f"budget tidak valid: {value!r} (bool bukan anggaran)")
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value > 0 else 0
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(f"budget tidak valid: {value!r} bukan angka") from exc
        return int(parsed) if parsed > 0 else 0
    raise ValueError(f"budget tidak valid: {type(value).__name__}")


@dataclass
class ContextBudgetReport:
    """Telemetri alokasi budget. Tidak mengandung isi konteks (aman di-log)."""

    budget: int = 0
    normalized_from: Any = None
    invariant_chars: int = 0
    body_chars: int = 0
    total_chars: int = 0
    invariant_overflow: bool = False
    truncated_sections: list[str] = field(default_factory=list)
    dropped_sections: list[str] = field(default_factory=list)
    section_chars: dict[str, int] = field(default_factory=dict)

    @property
    def unbounded(self) -> bool:
        return self.budget <= 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget,
            "normalized_from": self.normalized_from,
            "unbounded": self.unbounded,
            "invariant_chars": self.invariant_chars,
            "body_chars": self.body_chars,
            "total_chars": self.total_chars,
            "invariant_overflow": self.invariant_overflow,
            "truncated_sections": list(self.truncated_sections),
            "dropped_sections": list(self.dropped_sections),
            "section_chars": dict(self.section_chars),
        }


@dataclass(frozen=True)
class ContextSection:
    """Satu potongan konteks berprioritas.

    `priority` menentukan urutan pemotongan saat budget menipis (yang rendah
    dipotong lebih dulu). `name` dipakai untuk marker/telemetry.
    """

    name: str
    text: str
    priority: int = 0
    provenance: str = ""
    #: Pinned = keamanan/scope. Tidak pernah dipotong; memakan budget lebih
    #: dulu. Setara dengan blok `invariant` argumen, tetapi tetap sebagai
    #: section bernama sehingga call site tidak perlu dua konsep.
    pinned: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "chars": len(self.text),
            "pinned": self.pinned,
            "provenance": self.provenance,
        }


class ContextBuilder:
    """Dependencies are injected at construction (each call site wires only
    what it actually has available); a source whose dependency is `None`
    contributes an empty string rather than raising."""

    def __init__(
        self,
        *,
        action_memory: Any = None,
        asset_graph: Any = None,
        learning: Any = None,
        skill_router: Any = None,
        jobs: Any = None,
    ) -> None:
        self.action_memory = action_memory
        self.asset_graph = asset_graph
        self.learning = learning
        self.skill_router = skill_router
        # Callable returning the recall text for the CURRENT state's target.
        # A callable, not a string, because the block depends on which target is
        # in scope at the moment the context is built.
        self.jobs = jobs

    def build(
        self, state: Any, *, sources: list[str], char_budget: int = 0, user_input: str = "",
    ) -> str:
        """Session-summary lines are ALWAYS included (unconditional preamble
        -- matches `SupervisorAgent._build_context_block()`'s pre-existing
        behavior exactly, migrated here rather than changed), followed by
        the selected `sources` in the order given. `char_budget<=0` means
        unbounded (no truncation)."""
        parts = [self._session_summary(state)]
        for name in sources:
            piece = self._resolve(name, state, user_input=user_input)
            if piece:
                parts.append(piece)
        text = "\n".join(p for p in parts if p)
        return text[:char_budget] if char_budget > 0 else text

    def _session_summary(self, state: Any) -> str:
        cov = self.asset_graph.coverage() if self.asset_graph else {}
        return "\n".join([
            f"Session: {state.session_id}",
            f"Target: {state.scope.target or 'unset'}",
            f"Authorized: {state.scope.authorized}",
            f"Permission: {state.permission_mode.value}",
            f"Phase: {state.current_phase.value}",
            f"Graph nodes: {cov.get('nodes', 0)} coverage: {cov.get('coverage_pct', 0)}%",
            f"Queued tasks: {len(state.task_queue)}",
        ])

    def _resolve(self, name: str, state: Any, *, user_input: str) -> str:
        if name == "action_memory":
            return self.action_memory.context_block() if self.action_memory else ""
        if name == "md_context":
            from wth_ai.memory.md_context import context_block as md_block
            return md_block()
        if name == "engagement_graph":
            if not self.asset_graph or not self.asset_graph.nodes:
                return ""
            return self.asset_graph.export_for_planner()
        if name == "learning":
            if not self.learning:
                return ""
            tech_profile = state.metadata.get("tech_profile", "generic_web")
            return self.learning.build_planner_context(tech_profile)
        if name == "skill_fusion":
            if not self.skill_router or not user_input:
                return ""
            return self.skill_router.fuse(user_input).to_context()
        if name == "fts_recall":
            # ADR-004: SessionFtsIndex is explicitly "agent-pull-only lewat
            # tool session_search" -- there's no natural auto-query to
            # search for at context-assembly time, so this source
            # deliberately contributes nothing (not a missing feature).
            return ""
        if name == "jobs":
            # The bridge the chat surface lacked: recon results live in the job
            # store, chat context comes from the session asset graph, so without
            # this the agent answered "belum ada recon di sesi ini" while
            # completed jobs for the same target sat on disk.
            if self.jobs is None:
                return ""
            target = getattr(getattr(state, "scope", None), "target", "") or ""
            try:
                return self.jobs(target) or ""
            except Exception:  # noqa: BLE001 - recall must never break a turn
                return ""
        return ""


class BudgetedContextBuilder:
    """Context assembly dengan alokasi budget berprioritas (prioritas #3).

    Bug yang ditutup: `ContextBuilder.build(char_budget=N)` lama hanya
    melakukan `text[:N]`. Karena preamble invarian (scope / authorized /
    permission / phase) berada di AWAL, budget kecil tidak membuangnya -- tapi
    `[:N]` juga membuang EKOR dari satu blok raksasa secara buta, tanpa
    mempertimbangkan prioritas sumber dan tanpa marker, sehingga bagian yang
    hilang tidak terlihat oleh model maupun operator.

    Kontrak:
    * Invarian (scope, authorized, permission, phase, queued tasks) SELALU
      disertakan dan tidak pernah dipotong. Ini yang membuat context aman
      dipadatkan: model tidak boleh kehilangan batas izin/scope.
    * Sumber lain dialokasikan berdasarkan `priority` (lihat SOURCE_PRIORITY);
      saat budget menipis, prioritas rendah dipotong lebih dulu.
    * Potongan besar memakai head+tail (bukan hanya head) supaya error/summary
      di ekor tidak hilang, dan selalu diberi marker truncation eksplisit.
    * `budget<=0` berarti unbounded (kompatibel dengan perilaku lama).
    """

    def __init__(
        self,
        *,
        base: ContextBuilder | None = None,
        jobs: Any = None,
    ) -> None:
        self.base = base or ContextBuilder(jobs=jobs)

    # -- invarian ---------------------------------------------------------

    def invariant_block(self, state: Any) -> str:
        """Blok keamanan minimum. Sengaja kecil, deterministik, tak dipotong."""
        scope = getattr(state, "scope", None)
        permission = getattr(getattr(state, "permission_mode", None), "value", "")
        phase = getattr(getattr(state, "current_phase", None), "value", "")
        tasks = getattr(state, "task_queue", None) or []
        return "\n".join([
            f"[INVARIANT] Target: {getattr(scope, 'target', None) or 'unset'}",
            f"[INVARIANT] Authorized: {getattr(scope, 'authorized', False)}",
            f"[INVARIANT] Permission: {permission}",
            f"[INVARIANT] Phase: {phase}",
            f"[INVARIANT] Queued tasks: {len(tasks)}",
            "[INVARIANT] Out-of-scope rules must be honored; never infer authorization.",
        ])

    # -- section rendering ------------------------------------------------

    def render_section(self, section: ContextSection, budget: Any) -> str:
        """Render satu section dalam batas `budget` char, head+tail + marker.

        Marker truncation DIHITUNG sebagai bagian dari budget (bug lama: marker
        ditambahkan di luar batas sehingga output bisa melebihi budget).
        `budget<=0`/None = unbounded (dinormalisasi eksplisit).
        """
        limit = normalize_budget(budget)
        text = section.text or ""
        if limit == 0 or len(text) <= limit:
            return text
        dropped_full = len(text)
        # Marker length adapts to the budget: a fixed 40-char marker would push
        # the output over a smaller budget (verified: budget=39 produced 42).
        marker_full = _TRUNCATION_MARKER.format(dropped=dropped_full)
        if limit <= len(marker_full):
            marker = f"…[+{dropped_full}]…"
            if limit <= len(marker):
                return text[:limit]
            usable = limit - len(marker)
            head_n = usable // 2
            tail_n = usable - head_n
            return text[:head_n] + marker + (text[-tail_n:] if tail_n > 0 else "")
        usable = limit - len(marker_full)
        head_n = usable // 2
        tail_n = usable - head_n
        dropped = len(text) - head_n - tail_n
        body = _TRUNCATION_MARKER.format(dropped=max(0, dropped))
        return text[:head_n] + body + (text[-tail_n:] if tail_n > 0 else "")

    def build_from_sections(
        self,
        sections: list[ContextSection],
        *,
        budget: Any = 0,
        invariant: str = "",
        overflow: str = "raise",
        report: ContextBudgetReport | None = None,
    ) -> str:
        """Susun dari section eksplisit; invarian dijamin ikut utuh.

        Kontrak budget (EXPLISIT, menggantikan docstring lama yang keliru):
        * `budget` membatasi **body** (section), bukan invarian.
        * Invariant block adalah kontrak keamanan: TIDAK PERNAH dipotong.
        * Jika `len(invariant) > budget`:
            - `overflow="raise"` (default) -> `BudgetExceededError`;
            - `overflow="keep_invariant"` -> invarian utuh, body dipangkas,
              dilaporkan lewat `report.invariant_overflow`.
        * `budget<=0` = unbounded (kompatibel dengan perilaku lama).
        """
        if overflow not in ("raise", "keep_invariant"):
            raise ValueError("overflow harus 'raise' atau 'keep_invariant'")
        limit = normalize_budget(budget)
        rep = report if report is not None else ContextBudgetReport()
        rep.budget = limit
        # Rekam input asli bila normalisasi benar-benar mengubah nilai:
        # negatif, float/string numerik, atau bool (bool sudah ditolak).
        # `None`/0/positif-int tidak dianggap "dinormalisasi".
        if (
            budget is not None
            and (
                not isinstance(budget, int)
                or isinstance(budget, bool)
                or budget != limit
            )
        ):
            rep.normalized_from = budget

        for sec in sections:
            if not sec.name:
                raise ValueError("ContextSection.name wajib diisi")
        pinned_text = "\n".join(
            s.text for s in sorted(sections, key=lambda s: (-s.priority, s.name))
            if s.pinned and s.text
        )
        if pinned_text:
            # Pinned sections join the invariant block: same guarantee, one
            # mechanism. A call site can either pass `invariant=` or mark
            # sections `pinned=True`, never both formats drifting apart.
            invariant = "\n".join(p for p in (invariant, pinned_text) if p)
            sections = [s for s in sections if not s.pinned]
        ordered = sorted(sections, key=lambda s: (-s.priority, s.name))
        rep.invariant_chars = len(invariant or "")

        if limit == 0:
            for sec in ordered:
                if sec.text:
                    rep.section_chars[sec.name] = rep.section_chars.get(sec.name, 0) + len(sec.text)
            body = "\n".join(s.text for s in ordered if s.text)
            rep.body_chars = len(body)
            rep.total_chars = rep.invariant_chars + rep.body_chars
            return "\n".join(p for p in (invariant, body) if p)

        if rep.invariant_chars >= limit:
            # `>=`: the invariant alone fills (or exceeds) the requested budget.
            # There is no room left for any body section.
            if overflow == "raise" and rep.invariant_chars > limit:
                raise BudgetExceededError(invariant_len=rep.invariant_chars, budget=limit)
            rep.invariant_overflow = True
            # The invariant is not truncated, so it CONSUMES the body budget:
            # with an oversized safety block there is no body allowance left.
            # This is explicit (reported) instead of silently oversizing.
            remaining = 0
        else:
            remaining = limit
        kept: list[str] = []
        for sec in ordered:
            if not sec.text:
                continue
            # Reserve one char for the newline joiner when something is already
            # kept, so `len("\n".join(kept)) <= budget` holds exactly instead of
            # overrunning by (n-1). CRITICAL: `allocation<=0` means NO allowance
            # here -- it must not be passed to `render_section`, where `0` means
            # "unbounded" (verified bug: skill context of 20000 chars slipped
            # through after the budget was exhausted).
            allocation = remaining - (1 if kept else 0)
            if allocation <= 0:
                rep.dropped_sections.append(sec.name)
                continue
            rendered = self.render_section(sec, allocation)
            if not rendered:
                rep.dropped_sections.append(sec.name)
                continue
            if len(rendered) < len(sec.text):
                rep.truncated_sections.append(sec.name)
            rep.section_chars[sec.name] = rep.section_chars.get(sec.name, 0) + len(rendered)
            kept.append(rendered)
            remaining -= len(rendered)
        body = "\n".join(kept)
        rep.body_chars = len(body)
        rep.total_chars = rep.invariant_chars + rep.body_chars
        return "\n".join(p for p in (invariant, body) if p)

    def build(
        self,
        state: Any,
        *,
        sources: list[str],
        budget: Any = 0,
        user_input: str = "",
        provenance: dict[str, str] | None = None,
        overflow: str = "keep_invariant",
        report: ContextBudgetReport | None = None,
    ) -> str:
        """Bangun context dengan budget berprioritas.

        Kontrak budget (lihat `build_from_sections`): `budget` membatasi BODY;
        invarian tidak pernah dipotong. Default jalur produksi ini adalah
        `overflow="keep_invariant"`: invarian tetap utuh dan body dipangkas
        (dilaporkan), bukan menggagalkan turn. Pemanggil yang butuh fail-fast
        dapat memilih `overflow="raise"`.
        """
        invariant = self.invariant_block(state)
        pieces: list[ContextSection] = []
        for name in sources:
            text = self.base._resolve(name, state, user_input=user_input)
            if not text:
                continue
            pieces.append(
                ContextSection(
                    name=name,
                    text=text,
                    priority=SOURCE_PRIORITY.get(name, 0),
                    provenance=(provenance or {}).get(name, name),
                )
            )
        return self.build_from_sections(
            pieces, budget=budget, invariant=invariant, overflow=overflow, report=report,
        )
