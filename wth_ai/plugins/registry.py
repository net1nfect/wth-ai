"""Plugin registry — register + discover + invoke plugins by category."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PLUGIN_CATEGORIES: tuple[str, ...] = (
    "recon", "executor", "scoring", "analyzer", "visual", "reporting",
)


@dataclass
class Plugin:
    name: str
    category: str
    handler: Callable[..., Any]
    description: str = ""
    enabled: bool = True

    def run(self, *args: Any, **kwargs: Any) -> Any:
        return self.handler(*args, **kwargs)


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, dict[str, Plugin]] = {c: {} for c in PLUGIN_CATEGORIES}

    def register(self, category: str, name: str, handler: Callable[..., Any],
                 *, description: str = "", enabled: bool = True) -> Plugin:
        if category not in PLUGIN_CATEGORIES:
            raise ValueError(f"unknown plugin category '{category}'")
        plugin = Plugin(name=name, category=category, handler=handler,
                        description=description, enabled=enabled)
        self._plugins[category][name] = plugin
        return plugin

    def get(self, category: str, name: str) -> Plugin | None:
        return self._plugins.get(category, {}).get(name)

    def list(self, category: str | None = None) -> list[Plugin]:
        cats = [category] if category else list(PLUGIN_CATEGORIES)
        out: list[Plugin] = []
        for c in cats:
            out.extend(self._plugins.get(c, {}).values())
        return out

    def run_category(self, category: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Run every enabled plugin in a category; isolate failures."""
        results: dict[str, Any] = {}
        for name, plugin in self._plugins.get(category, {}).items():
            if not plugin.enabled:
                continue
            try:
                results[name] = plugin.run(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — one plugin never breaks others
                results[name] = {"error": str(exc)}
        return results

    def count(self) -> int:
        return sum(len(v) for v in self._plugins.values())

    def discover(self, plugins_dir: Path | None = None) -> int:
        """Import ``*.py`` from a plugins dir; each may call ``register_plugin``.

        Returns the number of plugin modules loaded. Never raises.
        """
        if plugins_dir is None:
            try:
                from wth_ai.workspace import wth_home
                plugins_dir = wth_home() / "plugins"
            except Exception:  # noqa: BLE001
                return 0
        plugins_dir = Path(plugins_dir)
        if not plugins_dir.exists():
            return 0
        loaded = 0
        for path in sorted(plugins_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"wth_plugin_{path.stem}", path)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    loaded += 1
            except Exception:  # noqa: BLE001 — a bad plugin must not crash the app
                continue
        return loaded


_REGISTRY: PluginRegistry | None = None


def get_plugin_registry() -> PluginRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = PluginRegistry()
    return _REGISTRY


def register_plugin(category: str, name: str, *, description: str = "",
                    enabled: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a plugin on the global registry."""
    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        get_plugin_registry().register(category, name, fn, description=description, enabled=enabled)
        return fn
    return _wrap
