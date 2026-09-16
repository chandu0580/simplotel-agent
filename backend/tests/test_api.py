import anthropic
import httpx

from tests.conftest import text_response, tool_response


def _api_status_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request)
    return anthropic.APIStatusError("upstream failure", response=response, body=None)


# ---------- Basic endpoints ----------


def test_health_reports_ai_mode(ai_client):
    assert ai_client.get("/api/health").json() == {"status": "ok", "mode": "ai"}


def test_health_reports_offline_mode(offline_client):
    assert offline_client.get("/api/health").json() == {"status": "ok", "mode": "offline"}


def test_responses_carry_request_id_header(offline_client):
    response = offline_client.get("/api/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"


def test_hotel_info_exposes_no_secrets(offline_client):
    body = offline_client.get("/api/hotel").json()
    assert body["hotel"]["name"] == "The Palm Grove Resort"
    assert body["today"] == "2026-10-05"
    assert "key" not in str(body).lower()


# ---------- Availability endpoint ----------


def test_availability_endpoint_returns_rooms(offline_client):
    response = offline_client.post(
        "/api/availability", json={"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2, "children": 0}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["nights"] == 2


def test_availability_endpoint_rejects_business_rule_violation(offline_client):
    response = offline_client.post("/api/availability", json={"check_in": "2026-10-09", "check_out": "2026-10-07", "adults": 2})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_booking_details"


def test_availability_endpoint_rejects_malformed_input(offline_client):
    response = offline_client.post("/api/availability", json={"check_in": "not-a-date", "check_out": "2026-10-09", "adults": 0})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert {d["field"] for d in body["error"]["details"]} == {"check_in", "adults"}


# ---------- Chat validation ----------


def test_chat_rejects_blank_and_oversized_messages(offline_client):
    assert offline_client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert offline_client.post("/api/chat", json={"message": "x" * 1001}).status_code == 422


def test_chat_rejects_unknown_fields_and_long_history(offline_client):
    assert offline_client.post("/api/chat", json={"message": "hi", "api_key": "x"}).status_code == 422
    history = [{"role": "user", "content": "hi"}] * 21
    assert offline_client.post("/api/chat", json={"message": "hi", "history": history}).status_code == 422


# ---------- AI mode: grounded answers ----------


def test_ai_answer_returns_cited_sources(ai_client, fake_messages):
    fake_messages.responses.append(
        text_response(
            {
                "type": "answer",
                "text": "Check-in is from 2:00 PM and check-out is by 11:00 AM.",
                "source_ids": ["timings.check_in_out"],
                "suggestions": ["Can I check in early?"],
            }
        )
    )

    response = ai_client.post("/api/chat", json={"message": "What time is check-in?"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "ai"
    assert body["reply"]["type"] == "answer"
    assert body["reply"]["sources"] == [{"id": "timings.check_in_out", "title": "Check-in and check-out times"}]
    call = fake_messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<guest_message>\nWhat time is check-in?\n</guest_message>" in call["messages"][-1]["content"]
    assert "Today's date at the hotel: 2026-10-05 (Monday)" in call["messages"][-1]["content"]


def test_ai_answer_without_valid_sources_is_downgraded_to_fallback(ai_client, fake_messages, kb):
    fake_messages.responses.append(
        text_response({"type": "answer", "text": "Yes, we have a rooftop casino.", "source_ids": ["amenities.casino"], "suggestions": []})
    )

    body = ai_client.post("/api/chat", json={"message": "Do you have a casino?"}).json()

    assert body["reply"]["type"] == "fallback"
    assert "casino" not in body["reply"]["text"]
    assert kb.hotel.phone in body["reply"]["text"]
    assert body["reply"]["sources"] == []


def test_ai_fallback_always_includes_contact_details(ai_client, fake_messages, kb):
    fake_messages.responses.append(
        text_response({"type": "fallback", "text": "I don't have details about spa packages for couples.", "source_ids": [], "suggestions": []})
    )

    body = ai_client.post("/api/chat", json={"message": "Do you have couples spa packages?"}).json()

    assert body["reply"]["type"] == "fallback"
    assert kb.hotel.phone in body["reply"]["text"]


# ---------- AI mode: tools ----------


def test_ai_availability_tool_call_runs_deterministic_search(ai_client, fake_messages):
    fake_messages.responses.append(
        tool_response("check_availability", {"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 3, "children": 0})
    )

    body = ai_client.post("/api/chat", json={"message": "Rooms for 3 adults from Wednesday for 2 nights?"}).json()

    assert body["reply"]["type"] == "availability"
    result = body["reply"]["availability"]
    assert {r["room_id"] for r in result["rooms"]} == {"deluxe-pool-view", "family-suite"}
    assert result["rooms"][0]["total_price"] == 15600
    assert len(fake_messages.calls) == 1  # prices never go back through the model


def test_ai_availability_tool_with_invalid_dates_asks_guest_to_fix_them(ai_client, fake_messages):
    fake_messages.responses.append(
        tool_response("check_availability", {"check_in": "2026-09-01", "check_out": "2026-09-03", "adults": 2, "children": 0})
    )

    body = ai_client.post("/api/chat", json={"message": "Rooms for 1st Sept?"}).json()

    assert body["reply"]["type"] == "collect_booking_details"
    assert "past" in body["reply"]["form_error"]
    assert body["reply"]["booking_prefill"]["check_in"] == "2026-09-01"


def test_ai_missing_details_shows_form_prefilled_from_context(ai_client, fake_messages):
    fake_messages.responses.append(
        tool_response(
            "request_booking_details",
            {"message": "Which dates would you like?", "check_in": None, "check_out": None, "adults": 3, "children": None},
        )
    )

    body = ai_client.post(
        "/api/chat",
        json={"message": "Do you have rooms for 3 of us?", "booking_context": {"check_in": "2026-10-07", "check_out": "2026-10-08", "children": 1}},
    ).json()

    reply = body["reply"]
    assert reply["type"] == "collect_booking_details"
    assert reply["text"] == "Which dates would you like?"
    assert reply["booking_prefill"] == {"check_in": "2026-10-07", "check_out": "2026-10-08", "adults": 3, "children": 1}


def test_new_check_in_from_model_is_not_mixed_with_old_check_out(ai_client, fake_messages):
    fake_messages.responses.append(
        tool_response(
            "request_booking_details",
            {"message": "How many nights?", "check_in": "2026-10-14", "check_out": None, "adults": None, "children": None},
        )
    )

    body = ai_client.post(
        "/api/chat",
        json={"message": "Can I stay next Wednesday instead?", "booking_context": {"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": 2}},
    ).json()

    assert body["reply"]["booking_prefill"] == {"check_in": "2026-10-14", "check_out": None, "adults": 2, "children": None}


def test_malformed_tool_arguments_degrade_to_offline(ai_client, fake_messages):
    fake_messages.responses.append(
        tool_response("check_availability", {"check_in": "2026-10-07", "check_out": "2026-10-09", "adults": "three", "children": 0})
    )

    response = ai_client.post("/api/chat", json={"message": "Rooms for three adults from 2026-10-07 to 2026-10-09?"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "offline"
    assert body["reply"]["type"] == "availability"


def test_unknown_tool_degrades_to_offline(ai_client, fake_messages):
    fake_messages.responses.append(tool_response("make_booking", {"room": "villa"}))

    body = ai_client.post("/api/chat", json={"message": "What time is check-in?"}).json()

    assert body["mode"] == "offline"


def test_unexpected_sdk_error_degrades_to_offline(ai_client, fake_messages):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    fake_messages.responses.append(anthropic.APIResponseValidationError(response=httpx.Response(200, request=request), body=None))

    body = ai_client.post("/api/chat", json={"message": "Is there a gym?"}).json()

    assert body["mode"] == "offline"
    assert body["reply"]["sources"][0]["id"] == "amenities.gym_spa"


# ---------- Conversation context ----------


def test_follow_up_sends_history_and_booking_context_to_model(ai_client, fake_messages):
    fake_messages.responses.append(
        text_response({"type": "answer", "text": "Yes, breakfast is included in the Deluxe Pool View Room.", "source_ids": ["amenities.dining"], "suggestions": []})
    )
    history = [
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "Which room is suitable for three guests?"},
        {"role": "assistant", "content": "The Deluxe Pool View Room sleeps three."},
    ]

    ai_client.post(
        "/api/chat",
        json={"message": "Does it include breakfast?", "history": history, "booking_context": {"adults": 3}},
    )

    messages = fake_messages.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]  # leading greeting dropped
    assert messages[1]["content"] == "The Deluxe Pool View Room sleeps three."
    assert "adults=3" in messages[-1]["content"]


# ---------- Failure and degradation ----------


def test_llm_api_error_degrades_to_offline_answer(ai_client, fake_messages):
    fake_messages.responses.append(_api_status_error(529))

    response = ai_client.post("/api/chat", json={"message": "What is the cancellation policy?"})

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "offline"
    assert "temporarily unavailable" in body["notice"]
    assert body["reply"]["type"] == "answer"
    assert body["reply"]["sources"][0]["id"] == "policies.cancellation"


def test_llm_connection_error_degrades_to_offline(ai_client, fake_messages):
    fake_messages.responses.append(anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com")))

    body = ai_client.post("/api/chat", json={"message": "Do you have a pool?"}).json()

    assert body["mode"] == "offline"
    assert body["reply"]["sources"][0]["id"] == "amenities.pool"


def test_model_refusal_and_malformed_output_degrade_to_offline(ai_client, fake_messages):
    fake_messages.responses.append(text_response("", stop_reason="refusal"))
    fake_messages.responses.append(text_response("not json at all"))

    first = ai_client.post("/api/chat", json={"message": "What time is check-in?"}).json()
    second = ai_client.post("/api/chat", json={"message": "What time is check-in?"}).json()

    assert first["mode"] == second["mode"] == "offline"


def test_offline_mode_when_ai_not_configured(offline_client):
    body = offline_client.post("/api/chat", json={"message": "Is breakfast included?"}).json()

    assert body["mode"] == "offline"
    assert body["notice"]
    assert body["reply"]["sources"][0]["id"] == "amenities.dining"


def test_unexpected_exception_returns_structured_500(ai_client, fake_messages):
    fake_messages.responses.append(RuntimeError("boom"))

    response = ai_client.post("/api/chat", json={"message": "hello"})

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert "boom" not in response.text
    assert body["request_id"] == response.headers["X-Request-ID"]
