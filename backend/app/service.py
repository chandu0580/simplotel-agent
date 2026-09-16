"""Chooses between the AI and offline assistants and applies graceful degradation."""

from datetime import date
import logging

from .claude_assistant import ClaudeAssistant, LLMError
from .offline import OfflineAssistant
from .schemas import ChatRequest, ChatResponse

logger = logging.getLogger("hotel_assistant.service")

AI_UNAVAILABLE_NOTICE = "Our AI assistant is temporarily unavailable, so answers are coming from our standard hotel FAQ."
AI_DISABLED_NOTICE = "AI answers are turned off; answers are coming from our standard hotel FAQ."


class ChatService:
    def __init__(self, offline: OfflineAssistant, ai: ClaudeAssistant | None):
        self.offline = offline
        self.ai = ai

    def handle(self, request: ChatRequest, request_id: str, today: date) -> ChatResponse:
        if self.ai is None:
            reply = self.offline.reply(request, today)
            logger.info("chat_reply request_id=%s mode=offline reason=ai_disabled type=%s", request_id, reply.type)
            return ChatResponse(request_id=request_id, mode="offline", reply=reply, notice=AI_DISABLED_NOTICE)

        try:
            reply = self.ai.reply(request, today)
        except LLMError as exc:
            logger.error("llm_failure request_id=%s error=%s", request_id, exc)
            reply = self.offline.reply(request, today)
            logger.info("chat_reply request_id=%s mode=offline reason=llm_failure type=%s", request_id, reply.type)
            return ChatResponse(request_id=request_id, mode="offline", reply=reply, notice=AI_UNAVAILABLE_NOTICE)

        logger.info(
            "chat_reply request_id=%s mode=ai type=%s sources=%s",
            request_id,
            reply.type,
            ",".join(s.id for s in reply.sources) or "-",
        )
        return ChatResponse(request_id=request_id, mode="ai", reply=reply)
