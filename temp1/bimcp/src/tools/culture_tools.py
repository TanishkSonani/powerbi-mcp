"""Translation/Culture CRUD tools — file-context only."""

from src.context.manager import ContextManager, FileContext
from src.tmdl.models import Culture, Translation

_FILE_CTX_ERROR = {
    "error": "Culture tools require an open PBIP folder. Use open_pbip_folder first."
}


def _require_file_ctx():
    """Return FileContext or an error dict."""
    try:
        ctx = ContextManager.get().get_active_context()
    except RuntimeError as exc:
        return {"error": str(exc)}
    if not isinstance(ctx, FileContext):
        return _FILE_CTX_ERROR
    return ctx


def list_cultures() -> dict:
    """List all cultures (languages) in the open model with their translation counts."""
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx
    return {
        "cultures": [
            {
                "name": c.name,
                "translation_count": len(c.translations),
            }
            for c in ctx.model_state.cultures.values()
        ],
        "count": len(ctx.model_state.cultures),
    }


def add_translation(
    culture_name: str,
    object_type: str,
    object_name: str,
    property_name: str,
    translated_value: str,
    table_name: str | None = None,
) -> dict:
    """
    Add or update a single translation for an object.
    
    Args:
        culture_name: Culture code (e.g., 'fr-FR', 'de-DE')
        object_type: 'Table', 'Measure', or 'Column'
        object_name: Name of the object to translate
        property_name: 'Caption', 'Description', or 'DisplayFolder'
        translated_value: The translated text
        table_name: Required for Measure and Column, the parent table name
    """
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Validate object_type
    if object_type not in ("Table", "Measure", "Column"):
        raise ValueError(
            f"Invalid object_type '{object_type}'. "
            "Must be 'Table', 'Measure', or 'Column'."
        )

    # Validate property_name
    if property_name not in ("Caption", "Description", "DisplayFolder"):
        raise ValueError(
            f"Invalid property_name '{property_name}'. "
            "Must be 'Caption', 'Description', or 'DisplayFolder'."
        )

    # Require table_name for Measure and Column
    if object_type in ("Measure", "Column") and not table_name:
        raise ValueError(
            f"table_name is required when object_type is '{object_type}'."
        )

    # Get or create culture
    if culture_name not in ctx.model_state.cultures:
        ctx.model_state.cultures[culture_name] = Culture(name=culture_name)

    culture = ctx.model_state.cultures[culture_name]

    # Check if translation already exists and update it
    for t in culture.translations:
        if (
            t.object_type == object_type
            and t.object_name == object_name
            and t.property_name == property_name
            and t.table_name == table_name
        ):
            t.translated_value = translated_value
            ctx.model_state._dirty = True
            return {
                "status": "updated",
                "culture": culture_name,
                "object": f"{object_type}:{object_name}",
                "property": property_name,
            }

    # Add new translation
    culture.translations.append(Translation(
        object_type=object_type,
        object_name=object_name,
        property_name=property_name,
        translated_value=translated_value,
        table_name=table_name,
    ))
    ctx.model_state._dirty = True

    return {
        "status": "created",
        "culture": culture_name,
        "object": f"{object_type}:{object_name}",
        "property": property_name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def bulk_add_translations(
    culture_name: str,
    translations: list[dict],
) -> dict:
    """
    Add multiple translations at once for a culture.
    
    Args:
        culture_name: Culture code (e.g., 'fr-FR')
        translations: List of translation dicts, each with:
            - object_type: 'Table', 'Measure', or 'Column'
            - object_name: Name of the object
            - property_name: 'Caption', 'Description', or 'DisplayFolder'
            - translated_value: The translated text
            - table_name: Required for Measure and Column (optional for Table)
    
    Returns:
        Summary of created and updated translations.
    """
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Get or create culture
    if culture_name not in ctx.model_state.cultures:
        ctx.model_state.cultures[culture_name] = Culture(name=culture_name)

    culture = ctx.model_state.cultures[culture_name]

    created = 0
    updated = 0
    errors: list[str] = []

    for i, t in enumerate(translations):
        try:
            object_type = t.get("object_type")
            object_name = t.get("object_name")
            property_name = t.get("property_name")
            translated_value = t.get("translated_value")
            table_name = t.get("table_name")

            # Validate required fields
            if not all([object_type, object_name, property_name, translated_value]):
                errors.append(
                    f"Translation {i}: Missing required field(s)"
                )
                continue

            # Validate object_type
            if object_type not in ("Table", "Measure", "Column"):
                errors.append(
                    f"Translation {i}: Invalid object_type '{object_type}'"
                )
                continue

            # Validate property_name
            if property_name not in ("Caption", "Description", "DisplayFolder"):
                errors.append(
                    f"Translation {i}: Invalid property_name '{property_name}'"
                )
                continue

            # Require table_name for Measure and Column
            if object_type in ("Measure", "Column") and not table_name:
                errors.append(
                    f"Translation {i}: table_name required for {object_type}"
                )
                continue

            # Check if translation exists
            found = False
            for existing in culture.translations:
                if (
                    existing.object_type == object_type
                    and existing.object_name == object_name
                    and existing.property_name == property_name
                    and existing.table_name == table_name
                ):
                    existing.translated_value = translated_value
                    updated += 1
                    found = True
                    break

            if not found:
                culture.translations.append(Translation(
                    object_type=object_type,
                    object_name=object_name,
                    property_name=property_name,
                    translated_value=translated_value,
                    table_name=table_name,
                ))
                created += 1

        except Exception as exc:
            errors.append(f"Translation {i}: {exc}")

    ctx.model_state._dirty = True

    result = {
        "status": "completed",
        "culture": culture_name,
        "created": created,
        "updated": updated,
        "total_in_culture": len(culture.translations),
    }
    if errors:
        result["errors"] = errors
    if created or updated:
        result["reminder"] = "Call save_model to persist changes to disk."

    return result
