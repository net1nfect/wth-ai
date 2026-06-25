"""Plugin architecture (Fase 6, flowchart PLUGIN).

A lightweight, dependency-free plugin registry covering the six plugin
categories from the spec: recon, executor, scoring, analyzer, visual, reporting.
Plugins are simple callables/objects registered under a category + name; they can
be registered in-process or auto-discovered from ``$WTH_HOME/plugins/*.py``.
"""

from wth_ai.plugins.registry import (
    PLUGIN_CATEGORIES,
    Plugin,
    PluginRegistry,
    get_plugin_registry,
    register_plugin,
)

__all__ = [
    "PLUGIN_CATEGORIES",
    "Plugin",
    "PluginRegistry",
    "get_plugin_registry",
    "register_plugin",
]
