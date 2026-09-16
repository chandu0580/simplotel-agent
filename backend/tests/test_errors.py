"""Stable error categories, one envelope, degradation reasons, no internals in responses."""

import shutil

from fastapi.testclient import TestClient
import pytest

from app.container import build_container
from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.errors import DEGRADATION_CODES, ErrorCode
from app.llm.provider import LLMProviderError
from app.main import create_app
from tests.conftest import GOA, TODAY

BASE = f"/api/v1/hotels/{GOA}"


def _assert_envelope(response, status, code):
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code and error["message"] and error["request_id"] == response.headers["X-Request-ID"]
    assert "Traceback" not in response.text and 'File "' not in response.text


def test_validation_error(offline_client):
    _assert_envelope(offline_client.post(f"{BASE}/availability", json={"adults": "many"}), 422, "VALIDATION_ERROR")


def test_malformed_json(offline_client):
    response = offline_client.post(f"{BASE}/conversations", content=b"{not json", headers={"Content-Type": "application/json"})
    _assert_envelope(response, 422, "VALIDATION_ERROR")


def test_unknown_route_and_hotel(offline_client):
    _assert_envelope(offline_client.get("/api/v1/nope"), 404, "NOT_FOUND")
    _assert_envelope(offline_client.post("/api/v1/hotels/hotel-xyz-999/conversations", json={}), 404, "HOTEL_NOT_FOUND")


def test_wrong_method_is_405_not_404(offline_client):
    _assert_envelope(offline_client.delete(f"{BASE}/availability"), 405, "METHOD_NOT_ALLOWED")


def test_oversized_body_is_rejected_before_parsing(offline_client):
    response = offline_client.post(f"{BASE}/conversations", content=b"x" * (70 * 1024), headers={"Content-Type": "application/json"})
    _assert_envelope(response, 413, "PAYLOAD_TOO_LARGE")


def test_knowledge_store_failure_is_knowledge_unavailable(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(Settings.for_tests().data_dir, data)
    (data / "hotels" / GOA / "hotel.json").write_text("{broken", encoding="utf-8")
    container = build_container(Settings.for_tests(data_dir=data), clock=FixedClock(TODAY))
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        response = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"})
    _assert_envelope(response, 503, "KNOWLEDGE_UNAVAILABLE")
    assert response.headers["Retry-After"] == "30" and "broken" not in response.text


def test_unhandled_exception_is_internal_error_without_details(offline_container, monkeypatch):
    def explode(*_args, **_kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(offline_container.conversations, "start", explode)
    with TestClient(create_app(container=offline_container), raise_server_exceptions=False) as client:
        response = client.post(f"{BASE}/conversations", json={})
    _assert_envelope(response, 500, "INTERNAL_ERROR")
    assert "secret internal detail" not in response.text


class FailingProvider:
    name = "failing"

    def __init__(self, kind):
        self.kind = kind

    def generate_with_tools(self, request):
        raise LLMProviderError(self.kind, "boom")

    def generate(self, request):
        raise LLMProviderError(self.kind, "boom")


@pytest.mark.parametrize(("kind", "code"), [("timeout", "LLM_TIMEOUT"), ("status", "LLM_UNAVAILABLE"), ("connection", "LLM_UNAVAILABLE")])
def test_model_failure_degrades_with_explicit_reason(kind, code):
    container = build_container(Settings.for_tests(), clock=FixedClock(TODAY), llm_provider=FailingProvider(kind))
    with TestClient(create_app(container=container)) as client:
        cid = client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
        response = client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"})
    assert response.status_code == 200
    degradation = response.json()["meta"]["degradation"]
    assert degradation["code"] == code and degradation["message"]
    assert "boom" not in response.text


def test_healthy_turn_has_no_degradation(offline_client):
    cid = offline_client.post(f"{BASE}/conversations", json={}).json()["conversation_id"]
    body = offline_client.post(f"{BASE}/conversations/{cid}/messages", json={"message": "What time is check-in?"}).json()
    assert body["meta"]["degradation"] is None  # AI disabled by configuration is not a degradation


def test_degradation_codes_are_dependency_categories():
    dependency = {ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_UNAVAILABLE, ErrorCode.TOOL_TIMEOUT, ErrorCode.TOOL_UNAVAILABLE, ErrorCode.RESERVATION_UNAVAILABLE}
    assert set(DEGRADATION_CODES.values()) <= dependency
