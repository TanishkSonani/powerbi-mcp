"""
LiveContext — communicates with a running Power BI Desktop instance via
the SOAP-based XMLA protocol exposed by the embedded msmdsrv.exe engine.

No .NET / pythonnet required: all communication is plain HTTP using the
`requests` library.  Analysis Services speaks standard XMLA over HTTP,
so every TOM/ADOMD capability we need (DAX queries, measure pushes,
metadata discovery) is reachable through SOAP envelopes.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import requests

# ---------------------------------------------------------------------------
# SOAP envelope templates
# ---------------------------------------------------------------------------

_DISCOVER_ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
  <Body>
    <Discover xmlns="urn:schemas-microsoft-com:xml-analysis">
      <RequestType>{request_type}</RequestType>
      <Restrictions>
        <RestrictionList>{restrictions}</RestrictionList>
      </Restrictions>
      <Properties>
        <PropertyList>
          <Catalog>{catalog}</Catalog>
        </PropertyList>
      </Properties>
    </Discover>
  </Body>
</Envelope>"""

_EXECUTE_ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.xmlsoap.org/soap/envelope/">
  <Body>
    <Execute xmlns="urn:schemas-microsoft-com:xml-analysis">
      <Command>
        <Statement>{statement}</Statement>
      </Command>
      <Properties>
        <PropertyList>
          <Catalog>{catalog}</Catalog>
          <Format>Tabular</Format>
        </PropertyList>
      </Properties>
    </Execute>
  </Body>
</Envelope>"""

# Rowset namespace used in XMLA Execute responses
_ROWSET_NS = "urn:schemas-microsoft-com:xml-analysis:rowset"
_XSD_NS = "http://www.w3.org/2001/XMLSchema"
_SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"


class LiveContext:
    """Stateless XMLA HTTP client for a single running Desktop model."""

    def __init__(self, port: int, model_name: str) -> None:
        self.port = port
        self.model_name = model_name
        self.connection_string = f"http://localhost:{port}/xmla"
        self._catalog = model_name

    # ------------------------------------------------------------------
    # Low-level XMLA helpers
    # ------------------------------------------------------------------

    def _discover(
        self,
        request_type: str,
        restrictions: dict | None = None,
        timeout: float = 10.0,
    ) -> str:
        """POST an XMLA Discover request, return raw XML response text."""
        restr_xml = ""
        if restrictions:
            restr_xml = "".join(
                f"<{k}>{v}</{k}>" for k, v in restrictions.items()
            )
        body = _DISCOVER_ENVELOPE.format(
            request_type=request_type,
            restrictions=restr_xml,
            catalog=self._catalog,
        )
        try:
            resp = requests.post(
                self.connection_string,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise ConnectionError(
                f"Cannot reach Desktop at {self.connection_string}: {exc}"
            ) from exc
        return resp.text

    def _execute(
        self,
        statement: str,
        timeout: float = 120.0,
    ) -> str:
        """POST an XMLA Execute request, return raw XML response text."""
        # Escape XML special characters in the statement
        escaped = (
            statement
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        body = _EXECUTE_ENVELOPE.format(
            statement=escaped,
            catalog=self._catalog,
        )
        try:
            resp = requests.post(
                self.connection_string,
                data=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8"},
                timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise ConnectionError(
                f"Cannot reach Desktop at {self.connection_string}: {exc}"
            ) from exc
        return resp.text

    def _parse_xmla_rowset(self, xml_text: str) -> dict:
        """
        Parse an XMLA Execute tabular rowset response.

        Returns {columns: list[str], rows: list[list], row_count: int}.
        Raises ValueError if the response is a SOAP Fault.
        """
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid XML in XMLA response: {exc}") from exc

        # Check for SOAP Fault
        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            faultstring = fault.findtext("faultstring") or "Unknown XMLA fault"
            raise ValueError(faultstring)

        # Also check for non-envelope faults (raw Fault element)
        fault2 = root.find(".//Fault")
        if fault2 is not None:
            faultstring = fault2.findtext("faultstring") or "Unknown XMLA fault"
            raise ValueError(faultstring)

        # Extract column names from the inline XSD schema
        columns: list[str] = []
        xsd_prefix = f"{{{_XSD_NS}}}"
        for elem in root.iter(f"{xsd_prefix}element"):
            name = elem.get("name")
            # Skip the root element row descriptor; only leaf elements are columns
            if name and name != "root" and name != "row":
                # Strip table prefix like "[Sales].[Amount]" → "Amount"
                col = name.split(".")[-1].strip("[]") if "." in name else name.strip("[]")
                columns.append(col)

        # Extract data rows from the rowset
        rows: list[list] = []
        rowset_prefix = f"{{{_ROWSET_NS}}}"
        for row_elem in root.iter(f"{rowset_prefix}row"):
            row: list = []
            for col in columns:
                child = row_elem.find(f"{rowset_prefix}{col}")
                if child is None:
                    # Try unnamespaced child (some AS versions omit namespace in row data)
                    child = row_elem.find(col)
                row.append(child.text if child is not None else None)
            rows.append(row)

        return {"columns": columns, "rows": rows, "row_count": len(rows)}

    @staticmethod
    def _format_markdown_table(columns: list[str], rows: list[list]) -> str:
        """Render columns + rows as a GitHub-flavoured markdown table."""
        if not columns:
            return "_no columns_"
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        data_rows = [
            "| " + " | ".join("" if v is None else str(v) for v in row) + " |"
            for row in rows
        ]
        return "\n".join([header, separator] + data_rows)

    # ------------------------------------------------------------------
    # Public operations
    # ------------------------------------------------------------------

    def execute_dax(self, dax_query: str, max_rows: int = 500) -> dict:
        """Execute a DAX query and return structured results."""
        xml_text = self._execute(dax_query)
        parsed = self._parse_xmla_rowset(xml_text)
        columns = parsed["columns"]
        rows = parsed["rows"]
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "markdown_table": self._format_markdown_table(columns, rows),
        }

    def get_model_info(self) -> dict:
        """Read metadata from the live AS instance."""
        # Tables
        tables_xml = self._discover("TMSCHEMA_TABLES")
        table_names: list[str] = []
        try:
            root = ET.fromstring(tables_xml)
            for name_elem in root.iter():
                local = name_elem.tag.split("}")[-1] if "}" in name_elem.tag else name_elem.tag
                if local == "Name" and name_elem.text:
                    table_names.append(name_elem.text.strip())
        except ET.ParseError:
            pass

        # Measures — count via TMSCHEMA_MEASURES
        measure_count = 0
        try:
            measures_xml = self._discover("TMSCHEMA_MEASURES")
            mroot = ET.fromstring(measures_xml)
            for elem in mroot.iter():
                local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local == "Name" and elem.text:
                    measure_count += 1
        except (ET.ParseError, ConnectionError):
            pass

        return {
            "model_name": self.model_name,
            "port": self.port,
            "connection_string": self.connection_string,
            "table_count": len(table_names),
            "measure_count": measure_count,
            "tables": table_names,
        }

    def push_measure(
        self,
        table_name: str,
        name: str,
        expression: str,
        format_string: str | None = None,
    ) -> dict:
        """Create or replace a measure in the live model via TMSL."""
        tmsl = {
            "createOrReplace": {
                "object": {
                    "database": self._catalog,
                    "table": table_name,
                    "measure": name,
                },
                "definition": {
                    "name": name,
                    "expression": expression,
                    "formatString": format_string or "",
                },
            }
        }
        xml_text = self._execute(json.dumps(tmsl))
        # If the response contains a Fault the execute call will have already
        # raised; if we get here it succeeded.
        try:
            root = ET.fromstring(xml_text)
            fault = root.find(
                ".//{http://schemas.xmlsoap.org/soap/envelope/}Fault"
            )
            if fault is not None:
                raise ValueError(fault.findtext("faultstring") or "XMLA fault")
        except ET.ParseError:
            pass  # non-XML success body is fine for TMSL commands

        return {
            "status": "pushed",
            "table_name": table_name,
            "measure_name": name,
        }

    def validate_expression(self, table_name: str, expression: str) -> dict:
        """Validate a DAX expression by evaluating it in the live model."""
        dax = f'EVALUATE ROW("Result", {expression})'
        try:
            result = self.execute_dax(dax, max_rows=1)
            first_val = result["rows"][0][0] if result["rows"] else None
            return {"valid": True, "result": str(first_val) if first_val is not None else None, "error": None}
        except ValueError as exc:
            return {"valid": False, "result": None, "error": str(exc)}

    def close(self) -> None:
        """No-op — XMLA is stateless HTTP; kept for interface symmetry."""
