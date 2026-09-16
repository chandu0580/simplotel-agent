"""Hotel operations API (v1): a minimal read-only slice behind the authorization boundary.

Writes (publishing content, changing AI configuration) are designed but not exposed: they need
real authentication (OIDC), an audit trail and a persistent store before they are safe.
"""

from fastapi import APIRouter, Request

from ..assistant.prompts import ANSWER_TOOL, PROMPT_VERSION, tool_schema_version
from ..auth.principal import Role
from ..core.clock import local_today
from ..llm.provider import LLMToolSpec
from ..schemas import V1ErrorResponse
from .deps import container_of, require_admin

router = APIRouter(
    prefix="/api/v1/admin/tenants/{tenant_id}",
    tags=["admin v1"],
    responses={401: {"model": V1ErrorResponse}, 403: {"model": V1ErrorResponse}, 404: {"model": V1ErrorResponse}},
)


@router.get("/hotels")
def list_hotels(tenant_id: str, request: Request):
    principal = require_admin(request, tenant_id, Role.HOTEL_STAFF)
    c = container_of(request)
    tenant = c.tenants.tenant(tenant_id)
    hotels = []
    for hotel_id in tenant.hotels:
        if principal.can_access_hotel(tenant_id, hotel_id):
            profile = c.knowledge.profile(hotel_id)
            hotels.append({"hotel_id": hotel_id, "name": profile.name, "city": profile.city, "languages": profile.languages})
    return {"tenant_id": tenant_id, "hotels": hotels}


@router.get("/hotels/{hotel_id}/knowledge")
def list_knowledge(tenant_id: str, hotel_id: str, request: Request, include_unpublished: bool = False):
    require_admin(request, tenant_id, Role.HOTEL_STAFF, hotel_id)
    c = container_of(request)
    today = local_today(c.clock, c.knowledge.profile(hotel_id).timezone)
    entries = c.knowledge.list_entries(hotel_id, include_unpublished=include_unpublished)
    return {
        "hotel_id": hotel_id,
        "as_of": today.isoformat(),
        "knowledge_version": c.knowledge.snapshot(hotel_id, today).knowledge_version,
        "entries": [{**e.model_dump(mode="json", exclude={"keywords"}), "servable_today": e.is_servable(today)} for e in entries],
    }


@router.get("/hotels/{hotel_id}/ai-config")
def ai_config(tenant_id: str, hotel_id: str, request: Request):
    require_admin(request, tenant_id, Role.HOTEL_ADMIN, hotel_id)
    c = container_of(request)
    tenant = c.tenants.tenant(tenant_id)
    today = local_today(c.clock, c.knowledge.profile(hotel_id).timezone)
    model_tools = c.tools.model_tools(tenant.feature_flags)
    specs = [ANSWER_TOOL] + [LLMToolSpec(d.name, d.description, d.input_schema) for d in model_tools]
    return {
        "hotel_id": hotel_id,
        "llm": {"provider": c.llm_provider.name if c.llm_provider else None, "ai_available": c.assistant.ai_available_for(tenant.feature_flags)},
        "model_routes": c.router.describe(),
        "versions": {
            "prompt_version": PROMPT_VERSION,
            "tool_schema_version": tool_schema_version(specs),
            "knowledge_version": c.knowledge.snapshot(hotel_id, today).knowledge_version,
        },
        "feature_flags": c.flags.snapshot(tenant.feature_flags),
        "tools": [
            {"name": d.name, "policy": d.policy.value, "exposed_to_model": d.exposed_to_model, "required_flag": d.required_flag, "version": d.version}
            for d in c.tools.definitions()
        ],
    }
