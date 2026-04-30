"""User-defined function (UDF) CRUD tools — file-context only."""

from src.context.manager import ContextManager, FileContext
from src.tmdl.models import UDF

_FILE_CTX_ERROR = {
    "error": "UDF tools require an open PBIP folder. Use open_pbip_folder first."
}

_VALID_RETURN_TYPES = {"variant", "string", "int64", "double", "datetime", "boolean"}


def _require_file_ctx():
    """Return FileContext or an error dict."""
    try:
        ctx = ContextManager.get().get_active_context()
    except RuntimeError as exc:
        return {"error": str(exc)}
    if not isinstance(ctx, FileContext):
        return _FILE_CTX_ERROR
    return ctx


def list_udfs() -> dict:
    """List all user-defined functions in the open model."""
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    _EXPR_PREVIEW_LEN = 80

    return {
        "udfs": [
            {
                "name": udf.name,
                "return_type": udf.return_type,
                "parameter_count": len(udf.parameters),
                "expression_preview": (
                    (udf.expression[:_EXPR_PREVIEW_LEN] + "…")
                    if len(udf.expression) > _EXPR_PREVIEW_LEN
                    else udf.expression
                ),
                "description": udf.description,
            }
            for udf in ctx.model_state.udfs.values()
        ],
        "count": len(ctx.model_state.udfs),
    }


def create_udf(
    name: str,
    expression: str,
    return_type: str = "variant",
    description: str | None = None,
    parameters: list[dict] | None = None,
) -> dict:
    """
    Create a new user-defined function (UDF).
    
    Args:
        name: Function name
        expression: DAX expression body
        return_type: Return type (variant, string, int64, double, datetime, boolean)
        description: Optional description
        parameters: Optional list of parameter dicts with 'name', 'type', 'description'
    
    Example parameter format:
        [{"name": "Amount", "type": "double", "description": "The amount value"}]
    """
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    # Validate return type
    if return_type not in _VALID_RETURN_TYPES:
        raise ValueError(
            f"Invalid return_type '{return_type}'. "
            f"Must be one of: {', '.join(sorted(_VALID_RETURN_TYPES))}"
        )

    # Check for duplicate
    if name in ctx.model_state.udfs:
        raise ValueError(f"UDF '{name}' already exists.")

    # Validate parameters if provided
    validated_params: list[dict] = []
    if parameters:
        for i, p in enumerate(parameters):
            param_name = p.get("name")
            if not param_name:
                raise ValueError(f"Parameter {i}: 'name' is required")
            param_type = p.get("type", "variant")
            if param_type not in _VALID_RETURN_TYPES:
                raise ValueError(
                    f"Parameter '{param_name}': Invalid type '{param_type}'"
                )
            validated_params.append({
                "name": param_name,
                "type": param_type,
                "description": p.get("description"),
            })

    ctx.model_state.udfs[name] = UDF(
        name=name,
        expression=expression,
        return_type=return_type,
        description=description,
        parameters=validated_params,
    )
    ctx.model_state._dirty = True

    return {
        "status": "created",
        "udf": name,
        "return_type": return_type,
        "parameter_count": len(validated_params),
        "reminder": "Call save_model to persist changes to disk.",
    }


def update_udf(
    udf_name: str,
    new_name: str | None = None,
    new_expression: str | None = None,
    return_type: str | None = None,
    description: str | None = None,
    parameters: list[dict] | None = None,
) -> dict:
    """
    Update an existing user-defined function.
    
    Args:
        udf_name: Current UDF name
        new_name: New name (optional)
        new_expression: New DAX expression (optional)
        return_type: New return type (optional)
        description: New description (optional)
        parameters: New parameters list (optional, replaces existing)
    """
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    udf = ctx.model_state.udfs.get(udf_name)
    if udf is None:
        available = list(ctx.model_state.udfs.keys())
        raise ValueError(
            f"UDF '{udf_name}' not found. Available: {available}"
        )

    # Validate return type if provided
    if return_type is not None and return_type not in _VALID_RETURN_TYPES:
        raise ValueError(
            f"Invalid return_type '{return_type}'. "
            f"Must be one of: {', '.join(sorted(_VALID_RETURN_TYPES))}"
        )

    # Apply updates
    if new_expression is not None:
        udf.expression = new_expression
    if return_type is not None:
        udf.return_type = return_type
    if description is not None:
        udf.description = description

    # Validate and update parameters if provided
    if parameters is not None:
        validated_params: list[dict] = []
        for i, p in enumerate(parameters):
            param_name = p.get("name")
            if not param_name:
                raise ValueError(f"Parameter {i}: 'name' is required")
            param_type = p.get("type", "variant")
            if param_type not in _VALID_RETURN_TYPES:
                raise ValueError(
                    f"Parameter '{param_name}': Invalid type '{param_type}'"
                )
            validated_params.append({
                "name": param_name,
                "type": param_type,
                "description": p.get("description"),
            })
        udf.parameters = validated_params

    # Handle rename
    final_name = udf_name
    if new_name and new_name != udf_name:
        if new_name in ctx.model_state.udfs:
            raise ValueError(f"UDF '{new_name}' already exists.")
        del ctx.model_state.udfs[udf_name]
        udf.name = new_name
        ctx.model_state.udfs[new_name] = udf
        final_name = new_name

    ctx.model_state._dirty = True

    return {
        "status": "updated",
        "udf": final_name,
        "reminder": "Call save_model to persist changes to disk.",
    }


def delete_udf(udf_name: str) -> dict:
    """Delete a user-defined function."""
    ctx = _require_file_ctx()
    if isinstance(ctx, dict):
        return ctx

    if udf_name not in ctx.model_state.udfs:
        available = list(ctx.model_state.udfs.keys())
        raise ValueError(
            f"UDF '{udf_name}' not found. Available: {available}"
        )

    del ctx.model_state.udfs[udf_name]
    ctx.model_state._dirty = True

    return {
        "status": "deleted",
        "udf": udf_name,
        "reminder": "Call save_model to persist changes to disk.",
    }
