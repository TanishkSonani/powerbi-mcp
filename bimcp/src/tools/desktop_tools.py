"""
Phase 3 — Desktop discovery and connection tools.
These tools manage the lifecycle of a LiveContext (Power BI Desktop via XMLA).
"""

from __future__ import annotations

from src.context.live_context import LiveContext
from src.context.manager import ContextManager
from src.discovery.port_finder import diagnose_discovery_failure, find_desktop_instances


def discover_desktop() -> dict:
    """Scan for running Power BI Desktop instances. Returns empty list if none found."""
    instances = find_desktop_instances()
    result = {"instances": instances, "count": len(instances)}
    if not instances:
        # Explain the empty result when the engine appears to be running, instead of
        # silently reporting zero (which previously hid a real configuration fault).
        reason = diagnose_discovery_failure()
        if reason:
            result["diagnostic"] = reason
    return result


def connect_desktop(model_name: str | None = None, port: int | None = None) -> dict:
    """
    Connect to a running Power BI Desktop model via XMLA.
    Specify model_name or port to select among multiple instances.
    If exactly one instance is running and neither is given, connects to it automatically.
    """
    instances = find_desktop_instances()

    if not instances:
        msg = (
            "No Power BI Desktop instance detected. "
            "Open a model in Desktop first."
        )
        reason = diagnose_discovery_failure()
        if reason:
            msg += f" Diagnostic: {reason}"
        return {"error": msg}

    target = None

    if port is not None:
        for inst in instances:
            if inst["port"] == port:
                target = inst
                break
        if target is None:
            available = ", ".join(
                f"{i['model_name']}@{i['port']}" for i in instances
            )
            return {"error": f"No instance found on port {port}. Available: {available}"}

    elif model_name is not None:
        needle = model_name.lower()
        for inst in instances:
            if inst["model_name"].lower() == needle:
                target = inst
                break
        if target is None:
            available = ", ".join(
                f"{i['model_name']}@{i['port']}" for i in instances
            )
            return {
                "error": (
                    f"No instance named {model_name!r}. "
                    f"Available: {available}"
                )
            }

    else:
        if len(instances) == 1:
            target = instances[0]
        else:
            available = ", ".join(
                f"{i['model_name']}@{i['port']}" for i in instances
            )
            return {
                "error": (
                    "Multiple Desktop instances found. "
                    f"Specify model_name or port. Available: {available}"
                )
            }

    catalog = target.get("catalog")  # present for Desktop TCP instances
    ContextManager.get().open_live_context(target["port"], target["model_name"], catalog)
    return {
        "status": "connected",
        "model_name": target["model_name"],
        "port": target["port"],
        "connection_string": target["connection_string"],
    }


def disconnect() -> dict:
    """Release the current context (file or live). Safe to call with no active context."""
    ContextManager.get().close_context()
    return {"status": "disconnected"}


def get_desktop_model_info() -> dict:
    """Return metadata from the live Desktop model (tables, measures, port)."""
    cm = ContextManager.get()
    if cm.context_type != "live":
        return {
            "error": (
                "Not connected to a live Desktop instance. "
                "Use connect_desktop first."
            )
        }
    ctx: LiveContext = cm.get_active_context()  # type: ignore[assignment]
    try:
        return ctx.get_model_info()
    except ConnectionError as exc:
        return {"error": str(exc)}
