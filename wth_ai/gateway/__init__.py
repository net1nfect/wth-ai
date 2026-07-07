"""Hermes-style gateway daemon — single process for sessions, worker, RPC."""

from wth_ai.gateway.client import GatewayClient, gateway_available
from wth_ai.gateway.daemon import GatewayDaemon

__all__ = ["GatewayDaemon", "GatewayClient", "gateway_available"]
