"""Feature flags: safe defaults, global overrides from env (FEATURE_<NAME>=true), per-tenant overrides."""

from collections.abc import Mapping

FLAG_DEFAULTS: dict[str, bool] = {
    "ai_assistant_enabled": True,
    "semantic_retrieval_enabled": False,
    "booking_tools_enabled": False,
    "whatsapp_enabled": False,
    "voice_enabled": False,
    "guardrail_price_check_enabled": True,
}


class FeatureFlags:
    def __init__(self, global_overrides: Mapping[str, bool] | None = None):
        overrides = dict(global_overrides or {})
        unknown = set(overrides) - set(FLAG_DEFAULTS)
        if unknown:
            raise ValueError(f"Unknown feature flags: {sorted(unknown)}")
        self._global = {**FLAG_DEFAULTS, **overrides}

    def is_enabled(self, name: str, tenant_overrides: Mapping[str, bool] | None = None) -> bool:
        if name not in FLAG_DEFAULTS:
            raise KeyError(f"Unknown feature flag {name!r}")
        if tenant_overrides and name in tenant_overrides:
            return bool(tenant_overrides[name])
        return self._global[name]

    def snapshot(self, tenant_overrides: Mapping[str, bool] | None = None) -> dict[str, bool]:
        return {name: self.is_enabled(name, tenant_overrides) for name in FLAG_DEFAULTS}
