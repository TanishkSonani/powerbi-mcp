"""
Discovers running Power BI Desktop instances by locating the embedded
Analysis Services engine (msmdsrv.exe) and the port it listens on.

Strategy:
  1. Primary   — read msmdsrv.port.txt from the AnalysisServicesWorkspaces folder
  2. Secondary — cross-reference `tasklist` + `netstat` if primary yields nothing
  3. Probe     — send a minimal XMLA Discover to each candidate port to confirm
                 the instance is alive and to read the model name
"""

from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))

# Power BI Desktop keeps its AS workspace in a different place per distribution.
# Only the classic path was scanned before, so Store/MSIX installs found nothing.
_WORKSPACE_ROOTS = (
    _LOCALAPPDATA / "Microsoft" / "Power BI Desktop" / "AnalysisServicesWorkspaces",
    _LOCALAPPDATA / "Microsoft" / "Power BI Desktop Store App" / "AnalysisServicesWorkspaces",
    _LOCALAPPDATA / "Packages" / "Microsoft.MicrosoftPowerBIDesktop_8wekyb3d8bbwe"
    / "LocalCache" / "Local" / "Microsoft" / "Power BI Desktop Store App"
    / "AnalysisServicesWorkspaces",
)

_XMLA_NS = "urn:schemas-microsoft-com:xml-analysis"

_DISCOVER_ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
  <Body>
    <Discover xmlns="urn:schemas-microsoft-com:xml-analysis">
      <RequestType>DISCOVER_XML_METADATA</RequestType>
      <Restrictions>
        <RestrictionList/>
      </Restrictions>
      <Properties>
        <PropertyList/>
      </Properties>
    </Discover>
  </Body>
</Envelope>"""

# ---------------------------------------------------------------------------
# Primary: read port files from the workspace folder
# ---------------------------------------------------------------------------

def find_desktop_port_files() -> list[dict]:
    """Return [{port, workspace_path}] from msmdsrv.port.txt across all known roots."""
    results: list[dict] = []
    for root in _WORKSPACE_ROOTS:
        try:
            if not root.exists():
                continue
            for ws_dir in root.iterdir():
                if not ws_dir.is_dir():
                    continue
                port_file = ws_dir / "Data" / "msmdsrv.port.txt"
                try:
                    # UTF-16 is possible here; decode leniently and keep digits only.
                    raw = port_file.read_text(encoding="utf-8", errors="ignore")
                    digits = "".join(ch for ch in raw if ch.isdigit())
                    if digits:
                        results.append({"port": int(digits), "workspace_path": str(ws_dir)})
                except (OSError, ValueError):
                    continue
        except OSError:
            continue
    return results


# ---------------------------------------------------------------------------
# Secondary: netstat + tasklist fallback
# ---------------------------------------------------------------------------

def find_desktop_ports_via_netstat() -> list[int]:
    """Find ports owned by msmdsrv.exe via tasklist + netstat."""
    try:
        tl = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq msmdsrv.exe"],
            capture_output=True, text=True, timeout=10,
        )
        pids: set[str] = set()
        for line in tl.stdout.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2:
                pids.add(parts[1])

        if not pids:
            return []

        ns = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        )
        ports: list[int] = []
        for line in ns.stdout.splitlines():
            cols = line.split()
            # cols: Proto  Local  Foreign  State  PID
            if len(cols) >= 5 and cols[3] == "LISTENING" and cols[4] in pids:
                m = re.search(r":(\d+)$", cols[1])
                if m:
                    port = int(m.group(1))
                    # msmdsrv listens on both IPv4 and IPv6, yielding the same port
                    # twice; de-duplicate while preserving discovery order.
                    if port not in ports:
                        ports.append(port)
        return ports
    except Exception:
        return []


def is_msmdsrv_running() -> bool:
    """True if the Power BI Desktop AS engine process exists (any distribution)."""
    try:
        tl = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq msmdsrv.exe"],
            capture_output=True, text=True, timeout=10,
        )
        return "msmdsrv.exe" in tl.stdout
    except Exception:
        return False


def diagnose_discovery_failure() -> str | None:
    """
    Explain why discovery found nothing, when it plausibly should have.

    Silent failure was how the hardcoded-ADOMD-path bug hid for so long: the engine
    was running and the port was open, but every probe returned None with no reason.
    """
    if not is_msmdsrv_running():
        return None  # genuinely nothing running — the normal empty case
    from src.context import ps_adomd_bridge as _bridge
    if not _bridge.is_available():
        return (
            "Power BI Desktop's analysis engine (msmdsrv.exe) is running, but the "
            "ADOMD client library could not be located, so the instance cannot be "
            "probed. Looked for 'Microsoft.PowerBI.AdomdClient.dll' in the classic "
            "install path, next to the running msmdsrv.exe, and under WindowsApps. "
            "Install Power BI Desktop, or ensure the running install is readable."
        )
    ports = find_desktop_ports_via_netstat()
    if not ports:
        return (
            "msmdsrv.exe is running but no listening TCP port was found for it. "
            "The model may still be loading — retry in a few seconds."
        )
    return (
        f"msmdsrv.exe is running and listening on {ports}, but the XMLA probe failed. "
        "The model may still be loading, or the connection was refused."
    )


# ---------------------------------------------------------------------------
# Probe: confirm an XMLA instance is alive and read its model name
# ---------------------------------------------------------------------------

def probe_xmla_instance(port: int, timeout: float = 2.0) -> dict | None:
    """
    Probe a port for an Analysis Services instance.

    Strategy:
      1. HTTP SOAP  — works for Azure AS / SSAS configured in HTTP mode
      2. PowerShell ADOMD.NET — works for Power BI Desktop embedded AS (TCP)

    Returns {model_name, port, connection_string} or None.
    """
    # --- try HTTP SOAP first ---
    url = f"http://localhost:{port}/xmla"
    try:
        resp = requests.post(
            url,
            data=_DISCOVER_ENVELOPE.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=timeout,
        )
        if resp.status_code == 200:
            model_name = f"model@{port}"
            try:
                root = ET.fromstring(resp.text)
                for elem in root.iter():
                    local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                    if local in ("DATABASE_ID", "CATALOG_NAME") and elem.text:
                        model_name = elem.text.strip()
                        break
            except ET.ParseError:
                pass
            return {"model_name": model_name, "port": port, "connection_string": url}
    except Exception:
        pass

    # --- fall back to PowerShell ADOMD.NET (Power BI Desktop TCP) ---
    try:
        from src.context.ps_adomd_bridge import probe_port as _ps_probe
        result = _ps_probe(port, timeout=max(timeout * 2, 5.0))
        if result:
            result["connection_string"] = f"localhost:{port}"
            return result
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def find_desktop_instances() -> list[dict]:
    """
    Discover all running Power BI Desktop Analysis Services instances.
    Returns a list of {model_name, port, connection_string} dicts sorted by port.
    """
    # Collect candidate ports — primary first, secondary if primary empty
    candidates: dict[int, str | None] = {}  # port → workspace_path or None

    for entry in find_desktop_port_files():
        candidates[entry["port"]] = entry["workspace_path"]

    # Always merge the netstat-derived ports rather than only when the primary scan
    # is empty: a stale msmdsrv.port.txt left by a crashed session yields a dead
    # candidate that would otherwise mask the genuinely running instance.
    for p in find_desktop_ports_via_netstat():
        candidates.setdefault(p, None)

    # Probe each candidate
    instances: list[dict] = []
    for port in sorted(candidates):
        result = probe_xmla_instance(port)
        if result is not None:
            instances.append(result)

    return instances
