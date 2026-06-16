"""Interactive /recon wizard — prompt for target domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wth_ai.orchestrator import WTHOrchestrator


class ReconWizard:
    """State machine for /recon without arguments."""

    PROMPT_TARGET = "target › "
    PROMPT_AUTH = "authorized › "

    @staticmethod
    def is_pending(state) -> bool:
        return bool(state.metadata.get("pending_prompt"))

    @staticmethod
    def pending_type(state) -> str | None:
        return state.metadata.get("pending_prompt")

    @staticmethod
    def start_target_prompt(orch: WTHOrchestrator) -> str:
        orch.state.metadata["pending_prompt"] = "recon_target"
        orch._persist()
        return (
            "## Recon Wizard\n\n"
            "Masukkan **domain target** (contoh: `app.example.com`):\n\n"
            "_Prompt berubah ke `target ›` — ketik domain lalu Enter._"
        )

    @staticmethod
    def handle_input(orch: WTHOrchestrator, user_input: str) -> str | None:
        """Returns response string if handled, None to continue normal routing."""
        pending = orch.state.metadata.get("pending_prompt")
        if not pending:
            return None
        if user_input.strip().startswith("/"):
            orch.state.metadata.pop("pending_prompt", None)
            orch.state.metadata.pop("wizard_target", None)
            orch._persist()
            return None

        text = user_input.strip()
        if not text:
            return "Input kosong — coba lagi."

        if pending == "recon_target":
            domain = text.replace("https://", "").replace("http://", "").split("/")[0].strip()
            orch.scope_mgr.set_scope(target=domain, in_scope=[domain, f"*.{domain}"])
            orch.state.scope = orch.scope_mgr.scope
            if not orch.state.scope.authorized:
                orch.state.metadata["pending_prompt"] = "recon_authorize"
                orch.state.metadata["wizard_target"] = domain
                orch._persist()
                return (
                    f"Target: **{domain}**\n\n"
                    "Apakah kamu punya **izin resmi** untuk testing target ini?\n"
                    "Ketik `yes` untuk melanjutkan atau `no` untuk batal."
                )
            orch.state.metadata.pop("pending_prompt", None)
            orch._persist()
            return orch._run_recon([domain])

        if pending == "recon_authorize":
            if text.lower() in {"yes", "y", "ya", "true", "1"}:
                orch.scope_mgr.set_scope(authorized=True)
                orch.state.scope = orch.scope_mgr.scope
                domain = orch.state.metadata.pop("wizard_target", orch.state.scope.target)
                orch.state.metadata.pop("pending_prompt", None)
                orch._persist()
                return orch._run_recon([domain] if domain else [])
            orch.state.metadata.pop("pending_prompt", None)
            orch.state.metadata.pop("wizard_target", None)
            orch._persist()
            return "Recon dibatalkan — scope tidak diotorisasi."

        return None

    @staticmethod
    def dynamic_prompt(state) -> str | None:
        p = state.metadata.get("pending_prompt")
        if p == "recon_target":
            return ReconWizard.PROMPT_TARGET
        if p == "recon_authorize":
            return ReconWizard.PROMPT_AUTH
        return None
