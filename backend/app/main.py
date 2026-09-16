import asyncio
from contextlib import asynccontextmanager
import logging

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import legacy, ops, v1_admin, v1_guest
from .api.errors import install_error_handlers
from .api.middleware import install_middleware
from .assistant.prompts import PROMPT_VERSION
from .container import Container, build_container
from .core.config import Settings
from .core.observability import configure_logging, log_event

logger = logging.getLogger("hotel_assistant.api")
PURGE_INTERVAL_SECONDS = 300


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = container.settings if container else (settings or Settings.from_env())
    configure_logging(settings.log_level, settings.log_format, settings.secret_values())
    container = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        anyio.to_thread.current_default_thread_limiter().total_tokens = settings.worker_threads
        log_event(
            logger,
            "startup",
            app_env=settings.app_env,
            llm_provider=container.llm_provider.name if container.llm_provider else None,
            model=settings.model_primary,
            prompt_version=PROMPT_VERSION,
            hotels=len(container.tenants.hotel_ids()),
            auth_mode=settings.auth_mode,
            worker_threads=settings.worker_threads,
            state_backend=settings.state_backend,
        )

        async def purge_expired_conversations():
            while True:
                await asyncio.sleep(PURGE_INTERVAL_SECONDS)
                purged = container.conversations.repository.purge_expired()
                if purged:
                    log_event(logger, "conversations_purged", count=purged)

        task = asyncio.create_task(purge_expired_conversations())
        yield
        # Graceful shutdown: uvicorn has already stopped accepting connections and drained in-flight
        # requests (bounded by uvicorn's --timeout-graceful-shutdown when set); now flush and close dependencies.
        task.cancel()
        container.close()
        log_event(logger, "shutdown_complete")

    docs = settings.expose_api_docs
    app = FastAPI(
        title="Hotel Guest Assistant API",
        version="1.1.0",
        description="Multi-tenant hotel guest assistant. Guest API under /api/v1/hotels/{hotel_id}; legacy /api/* endpoints are deprecated.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.container = container
    install_middleware(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID", "Authorization", "traceparent"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    install_error_handlers(app)
    for module in (ops, v1_guest, v1_admin, legacy):
        app.include_router(module.router)
    return app


app = create_app()
