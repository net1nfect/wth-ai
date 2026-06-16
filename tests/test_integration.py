"""Core integration tests."""

import tempfile
import unittest
from pathlib import Path

from core.config import H1T4MConfiguration
from core.database import ResultsDatabase
from core.exceptions import AuthorizationError
from core.orchestrator import H1T4M_Orchestrator
from core.state_machine import ExecutionState, ExecutionStateMachine
from utils.validators import enforce_scope


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Validate lifecycle integration."""

    async def test_state_machine(self) -> None:
        machine = ExecutionStateMachine()
        await machine.transition(ExecutionState.INITIALIZING)
        await machine.transition(ExecutionState.CLEANUP)
        await machine.transition(ExecutionState.IDLE)
        self.assertEqual(machine.current, ExecutionState.IDLE)

    async def test_smoke_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = H1T4MConfiguration(Path(directory) / "missing.yaml")
            config.engagement.authorization_proof = "test-authorization"
            config.database.url = f"sqlite:///{directory}/test.db"
            config.reporting.report_format = ["json"]
            config.threat_intel.enabled_feeds = []
            orchestrator = H1T4M_Orchestrator(config)
            result = await orchestrator.execute_engagement_lifecycle(
                "example.com",
                ["example.com"],
                ["safe smoke test"],
                {"technologies": [], "findings": [{"type": "weak_tls", "severity": "medium"}]},
            )
            self.assertTrue(result["engagement_id"])
            self.assertEqual(len(orchestrator.agents), 9)

    def test_database_transactional_storage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = ResultsDatabase(f"sqlite:///{directory}/test.db")
            engagement_id = database.create_engagement({"target": "example.com"})
            database.store_finding(engagement_id, {"type": "weak_tls"})
            self.assertEqual(len(database.query_findings({"engagement_id": engagement_id})), 1)

    def test_scope_enforcement(self) -> None:
        with self.assertRaises(AuthorizationError):
            enforce_scope("outside.test", ["example.com"], "proof")
