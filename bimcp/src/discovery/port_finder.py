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

_WORKSPACES_ROOT = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "Power BI Desktop"
    / "AnalysisServicesWorkspaces"
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
    """Return [{port, workspace_path}] from msmdsrv.port.txt files."""
    results: list[dict] = []
    if not _WORKSPACES_ROOT.exists():
        return results
    for ws_dir in _WORKSPACES_ROOT.iterdir():
        if not ws_dir.is_dir():
            continue
        port_file = ws_dir / "Data" / "msmdsrv.port.txt"
        try:
            port = int(port_file.read_text(encoding="utf-8").strip())
            results.append({"port": port, "workspace_path": str(ws_dir)})
        except (FileNotFoundError, ValueError):
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
                    ports.append(int(m.group(1)))
        return ports
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Probe: confirm an XMLA instance is alive and read its model name
# ---------------------------------------------------------------------------

def probe_xmla_instance(port: int, timeout: float = 2.0) -> dict | None:
    """
    POST a minimal XMLA Discover to localhost:{port}/xmla.
    Returns {model_name, port, connection_string} or None on failure.
    """
    url = f"http://localhost:{port}/xmla"
    try:
        resp = requests.post(
            url,
            data=_DISCOVER_ENVELOPE.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=timeout,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        return None
    except Exception:
        return None

    if resp.status_code != 200:
        return None

    model_name = f"model@{port}"
    try:
        root = ET.fromstring(resp.text)
        # XMLA responses nest data in rowset rows — search broadly
        for elem in root.iter():
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local in ("DATABASE_ID", "CATALOG_NAME") and elem.text:
                model_name = elem.text.strip()
                break
    except ET.ParseError:
        pass

    return {
        "model_name": model_name,
        "port": port,
        "connection_string": url,
    }


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

    if not candidates:
        for p in find_desktop_ports_via_netstat():
            candidates.setdefault(p, None)

    # Probe each candidate
    instances: list[dict] = []
    for port in sorted(candidates):
        result = probe_xmla_instance(port)
        if result is not None:
            instances.append(result)

    return instances
