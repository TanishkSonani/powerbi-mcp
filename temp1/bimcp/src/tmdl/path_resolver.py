"""
PBIP path resolver — accepts any of these as input and returns the definition/ folder:
  1. The definition/ folder directly  (contains model.tmdl)
  2. The .SemanticModel/ folder       (contains definition/model.tmdl)
  3. The PBIP root folder             (contains *.SemanticModel/definition/)
"""

from pathlib import Path


def resolve_tmdl_definition_path(user_path: str) -> Path:
    p = Path(user_path).expanduser().resolve()

    if not p.exists():
        raise ValueError(f"Path does not exist: {p}")

    # 1. Already the definition/ folder
    if (p / "model.tmdl").exists():
        return p

    # 2. .SemanticModel/ folder → look for definition/ inside
    if (p / "definition" / "model.tmdl").exists():
        return p / "definition"

    # 3. PBIP root → glob for any *.SemanticModel/definition/
    candidates = sorted(p.glob("*.SemanticModel/definition"))
    if len(candidates) == 1:
        if (candidates[0] / "model.tmdl").exists():
            return candidates[0]
        raise ValueError(
            f"Found .SemanticModel/definition at {candidates[0]} "
            "but it does not contain model.tmdl."
        )
    if len(candidates) > 1:
        names = [str(c) for c in candidates]
        raise ValueError(
            f"Multiple SemanticModel definitions found under {p}: {names}. "
            "Provide a more specific path."
        )

    raise ValueError(
        f"Cannot locate a TMDL definition/ folder in: {p}\n"
        "Expected one of:\n"
        "  • A definition/ folder containing model.tmdl\n"
        "  • A .SemanticModel/ folder containing definition/model.tmdl\n"
        "  • A PBIP root containing *.SemanticModel/definition/model.tmdl"
    )
