"""
ResourceProvider — serves the four Microsoft reference .md files as MCP resources.

Each .md file carries YAML frontmatter with:
    name:        display name
    description: one-line description (used for resource listing)
    uriTemplate: the MCP resource URI  (e.g. resource://dax_query_instructions_and_examples)

The provider strips the frontmatter before returning the body, so the LLM
receives clean markdown without metadata noise.
"""

import re
from pathlib import Path

import yaml
from mcp.types import Resource

# Resources live two levels above this file: bimcp/resources/md/
_MD_DIR = Path(__file__).parent.parent.parent / "resources" / "md"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_md(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text) for a .md file."""
    raw = path.read_text(encoding="utf-8-sig")  # utf-8-sig auto-strips BOM if present
    match = _FRONTMATTER_RE.match(raw)
    if match:
        meta = yaml.safe_load(match.group(1)) or {}
        body = raw[match.end():]
    else:
        meta = {}
        body = raw
    return meta, body


class ResourceProvider:
    """Scans resources/md/ at startup and caches metadata for fast serving."""

    def __init__(self) -> None:
        self._index: dict[str, tuple[Resource, str]] = {}  # uri → (Resource, body)
        self._load()

    def _load(self) -> None:
        if not _MD_DIR.exists():
            raise FileNotFoundError(
                f"Resource directory not found: {_MD_DIR}. "
                "Ensure resources/md/ exists and contains the four .md guides."
            )
        for md_file in sorted(_MD_DIR.glob("*.md")):
            meta, body = _parse_md(md_file)
            uri = meta.get("uriTemplate", f"resource://{md_file.stem}")
            resource = Resource(
                uri=uri,                                    # type: ignore[arg-type]
                name=meta.get("name", md_file.stem),
                description=meta.get("description", ""),
                mimeType="text/markdown",
            )
            self._index[uri] = (resource, body)

    # ------------------------------------------------------------------
    # Public interface called by server.py handlers
    # ------------------------------------------------------------------

    def list_resources(self) -> list[Resource]:
        return [r for r, _ in self._index.values()]

    def read_resource(self, uri: str) -> str:
        entry = self._index.get(uri)
        if entry is None:
            available = ", ".join(self._index.keys())
            raise ValueError(
                f"Resource not found: {uri!r}. "
                f"Available resources: {available}"
            )
        _, body = entry
        return body
