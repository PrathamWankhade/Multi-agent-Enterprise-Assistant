"""Multi-Agent Enterprise Assistant - FastAPI application."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings


class NoCacheStaticFiles(StaticFiles):
    """Serve static files with caching disabled so local edits show up instantly."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

settings = get_settings()

data_dir = Path(__file__).parent.parent / "data"


async def _seed_knowledge_base() -> None:
    """Ingest bundled sample documents on first run (when the store is empty)."""
    try:
        from app.rag.ingest import ingest_directory
        from app.rag.store import get_vector_store

        store = get_vector_store()
        await store.initialize()
        if not store.available:
            logger.warning("Vector store unavailable; skipping sample ingestion")
            return
        if await store.count() > 0:
            logger.info("Knowledge base already populated; skipping sample ingestion")
            return
        if not data_dir.exists():
            return
        ids = await ingest_directory(
            directory=str(data_dir),
            glob_pattern="*.md",
            doc_id_prefix="sample:",
            chunk_size=500,
            overlap=80,
        )
        logger.info("Seeded knowledge base with %s chunks", len(ids))
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Knowledge base seeding skipped: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _register_tools()
    await _seed_knowledge_base()
    yield


def _register_tools() -> None:
    """Register enterprise tools at startup (idempotent)."""
    try:
        from app.tools.enterprise import register_tools
        register_tools()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Tool registration skipped: %s", exc)


app = FastAPI(
    title="Multi-Agent Enterprise Assistant",
    description="Orchestrator, Research, Analyst, and Execution agents with RAG, memory, and Azure deployment.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")

# Serve frontend
frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", NoCacheStaticFiles(directory=str(frontend_dir)), name="static")

# Markdown sources for the in-app docs viewer (kept separate from /docs Swagger).
docs_dir = Path(__file__).parent.parent / "docs"
app.mount("/docs-md", NoCacheStaticFiles(directory=str(docs_dir)), name="docs-md")


@app.get("/readme.md", tags=["root"], include_in_schema=False)
async def root_readme():
    """Root README, used by the in-app documentation viewer."""
    return FileResponse(
        str(Path(__file__).parent.parent / "README.md"), media_type="text/markdown"
    )


@app.get("/documentation", tags=["root"], include_in_schema=False)
async def documentation_page():
    return FileResponse(str(frontend_dir / "docs.html"))


@app.get("/", tags=["root"])
async def root():
    return FileResponse(str(frontend_dir / "index.html"))


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logging.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": str(exc)},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
    )