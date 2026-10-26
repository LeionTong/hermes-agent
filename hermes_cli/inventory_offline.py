"""Offline provider/model inventory: local config and bundled catalogs, never the network.

``hermes providers list --offline`` reads this instead of
:func:`hermes_cli.inventory.build_models_payload`, which may consult models.dev, remote model
catalogs and configured custom endpoints. Rows use the picker payload's provider-row keys so one
serializer handles both. Only environment variable NAMES are read for readiness, and the
credential pool is never opened.
"""

from __future__ import annotations

import os

from hermes_cli.inventory import ConfigContext


def _config_model_ids(*values: object) -> list[str]:
    """Model ids from the supported local config shapes (str, list, dict keyed by id, ``{id|name}``)."""
    ids: list[str] = []
    for value in values:
        candidates = list(value) if isinstance(value, (dict, list, tuple)) else [value]
        for item in candidates:
            if isinstance(item, dict):
                item = item.get("id") or item.get("name")
            if isinstance(item, str) and item.strip() and item.strip() not in ids:
                ids.append(item.strip())
    return ids


def _env_var_names(slug: str) -> tuple[str, ...]:
    from hermes_cli.auth import PROVIDER_REGISTRY
    from hermes_cli.providers import get_provider

    config = PROVIDER_REGISTRY.get(slug) or get_provider(slug, allow_network=False)
    raw = getattr(config, "api_key_env_vars", ()) if config else ()
    return tuple(name for name in raw if isinstance(name, str) and name)


def _auth_type(slug: str) -> str:
    from hermes_cli.auth import PROVIDER_REGISTRY

    return getattr(PROVIDER_REGISTRY.get(slug), "auth_type", "api_key")


def _row(slug: str, name: str, models: list[str], *, is_current: bool, user_defined: bool, source: str,
         authenticated: bool = True, auth_type: str = "api_key", key_env: str = "") -> dict:
    return {"slug": slug, "name": name, "is_current": is_current, "is_user_defined": user_defined,
            "models": models, "total_models": len(models), "source": source,
            "authenticated": authenticated, "auth_type": auth_type, "key_env": key_env}


def _canonical_rows(ctx: ConfigContext, current: str, include_unconfigured: bool) -> list[dict]:
    from hermes_cli.models import _PROVIDER_MODELS, OPENROUTER_MODELS
    from hermes_cli.models_catalog_static import listed_canonical_providers

    excluded = {str(slug).strip().lower() for slug in ctx.excluded_providers or []}
    configured = {str(slug).strip().lower() for slug, value in ctx.user_providers.items()
                  if isinstance(value, dict) and str(slug).strip()} | ({current} if current else set())
    rows = []
    for entry in listed_canonical_providers():
        slug = entry.slug.lower()
        env_vars = _env_var_names(slug)
        is_configured = slug in configured or any(os.environ.get(name, "").strip() for name in env_vars)
        if slug in excluded or not (include_unconfigured or is_configured):
            continue
        user_row = ctx.user_providers.get(entry.slug)
        models = _config_model_ids(*(user_row.get(k) for k in ("default_model", "model", "models"))) \
            if isinstance(user_row, dict) else []
        if slug == current and ctx.current_model:
            models.insert(0, ctx.current_model)
        static = [mid for mid, _ in OPENROUTER_MODELS] if slug == "openrouter" else list(_PROVIDER_MODELS.get(slug, ()))
        rows.append(_row(entry.slug, entry.label, list(dict.fromkeys([*models, *static])), is_current=slug == current,
                         user_defined=False, source="static", authenticated=is_configured,
                         auth_type=_auth_type(slug), key_env=env_vars[0] if env_vars else ""))
    return rows


def _named_provider_rows(ctx: ConfigContext, current: str, seen: set[str]) -> list[dict]:
    """``providers:`` entries outside the canonical registry: declared models only, never probed."""
    rows = []
    for slug, value in ctx.user_providers.items():
        normalized = str(slug).strip().lower()
        if not isinstance(value, dict) or not normalized or normalized in seen:
            continue
        models = _config_model_ids(value.get("default_model"), value.get("model"), value.get("models"))
        rows.append(_row(str(slug), str(value.get("name") or slug), models, is_current=normalized == current,
                         user_defined=True, source="user-config", key_env=str(value.get("key_env") or "")))
        seen.add(normalized)
    return rows


def _endpoint_identity(entry: dict) -> tuple:
    """Endpoint + credential + protocol + headers. URL alone would merge distinct tenants or API modes
    that happen to share a gateway."""
    from hermes_cli.config import normalize_extra_headers

    url = str(entry.get("base_url") or entry.get("api") or entry.get("url") or "").strip().rstrip("/")
    inline_key = str(entry.get("api_key") or "").strip()
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "").strip()
    credential = inline_key or (f"env:{key_env}" if key_env else "")
    api_mode = str(entry.get("api_mode") or entry.get("transport") or "").strip().lower()
    return url, credential, api_mode, tuple(sorted(normalize_extra_headers(entry.get("extra_headers")).items()))


def _legacy_custom_rows(ctx: ConfigContext, current: str, seen: set[str]) -> list[dict]:
    """The legacy ``custom_providers`` list, minus entries identical to a keyed ``providers:`` entry."""
    known = {_endpoint_identity(value) for value in ctx.user_providers.values() if isinstance(value, dict)}
    current_url = str(ctx.current_base_url or "").strip().rstrip("/").lower()
    rows = []
    for entry in ctx.custom_providers:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            continue
        name = str(entry["name"]).strip()
        url = _endpoint_identity(entry)[0]
        slug = "custom:" + name.lower().replace(" ", "-")
        if (url and _endpoint_identity(entry) in known) or slug in seen:
            continue
        is_current = slug == current or (current == "custom" and bool(current_url) and url.lower() == current_url)
        rows.append(_row(slug, name, _config_model_ids(entry.get("model"), entry.get("models")), is_current=is_current,
                         user_defined=True, source="user-config", key_env=str(entry.get("key_env") or "")))
        seen.add(slug)
    return rows


def build_offline_models_payload(ctx: ConfigContext, *, include_unconfigured: bool = False) -> dict:
    """``{providers, model, provider}`` from config + bundled catalogs; no network, no credential pool."""
    current = str(ctx.current_provider or "").strip().lower()
    rows = _canonical_rows(ctx, current, include_unconfigured)
    seen = {row["slug"].lower() for row in rows}
    rows += _named_provider_rows(ctx, current, seen)
    rows += _legacy_custom_rows(ctx, current, seen)
    if current and current not in seen:
        models = [ctx.current_model] if ctx.current_model else []
        rows.append(_row(ctx.current_provider, ctx.current_provider, models, is_current=True, user_defined=True,
                         source="model-config"))
    return {"providers": rows, "model": ctx.current_model, "provider": ctx.current_provider}
