"""H1T4M-AI command-line entry point."""

from __future__ import annotations

import asyncio
import json

import click

from core.config import H1T4MConfiguration
from core.orchestrator import H1T4M_Orchestrator


@click.command()
@click.option("--config", "config_path", default="config/h1t4m_config.yaml", show_default=True)
@click.option("--target", default=None, help="Override configured target.")
@click.option("--scope", multiple=True, help="Override configured authorized scope.")
@click.option("--objective", multiple=True, help="Override configured objectives.")
def main(config_path: str, target: str | None, scope: tuple[str, ...], objective: tuple[str, ...]) -> None:
    """Run an authorization-first dry-run engagement."""
    config = H1T4MConfiguration(config_path)
    orchestrator = H1T4M_Orchestrator(config)
    result = asyncio.run(
        orchestrator.execute_engagement_lifecycle(
            target=target,
            scope=list(scope) or None,
            objectives=list(objective) or None,
        )
    )
    click.echo(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
