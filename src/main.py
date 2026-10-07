"""研析通 v2.0 — Application entry point."""

import asyncio
import logging, os

from dotenv import load_dotenv

load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from src.core.config import get_settings
from src.api.routes import router
from src.api.middleware import RateLimitMiddleware, InputSanitizationMiddleware
from src.observability.tracing import setup_tracing, instrument_fastapi

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _warmup_encoders() -> None:
    """预热论文库与知识库的 BGE-M3 编码器（线程池执行，不阻塞事件循环）。

    CPU 冷加载 + 首次编码约 35s；不预热则首个提问要多等半分钟以上。
    """
    def _warm() -> None:
        try:
            from src.knowledge.vector_store import get_vector_store
            from src.knowledge.kb import get_kb_store

            get_vector_store().warmup()
            get_kb_store().warmup()
            logger.info("Embedding encoders warm (paper store + KB store)")
        except Exception as e:  # 预热失败不影响服务，首次使用时仍会懒加载
            logger.warning("Embedding warmup skipped: %s", e)

    await asyncio.to_thread(_warm)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("研析通 v2.0 starting on %s:%s", settings.app.host, settings.app.port)
    setup_tracing()
    asyncio.create_task(_warmup_encoders())
    yield
    from src.auth.store import close_auth_store
    await close_auth_store()
    logger.info("研析通 v2.0 shutting down")


app = FastAPI(title="研析通 v2.0", version="3.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(RateLimitMiddleware, requests_per_minute=get_settings().rate_limit.requests_per_minute)
app.add_middleware(InputSanitizationMiddleware)

app.include_router(router)

instrument_fastapi(app)


# ===== 首页 =====
@app.get("/", include_in_schema=False)
async def root():
    html_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
    if os.path.exists(html_path):
        return HTMLResponse(open(html_path, encoding="utf-8").read())
    return HTMLResponse("<h1>研析通 v2.0</h1><p>Frontend not found. Copy index.html to templates/</p>")


# ===== Prometheus =====
@app.get("/metrics")
async def metrics():
    from src.observability.metrics import get_metrics_response
    return get_metrics_response()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run("src.main:app", host=settings.app.host, port=settings.app.port, reload=settings.app.debug)