# # 研析通 v2.0 — Application entry point with Phase 5 observability.
#
# import logging
# from contextlib import asynccontextmanager
# from fastapi import FastAPI
# from fastapi.middleware.cors import CORSMiddleware
#
# from src.core.config import get_settings
# from src.api.routes import router
# from src.api.middleware import RateLimitMiddleware, InputSanitizationMiddleware
# from src.observability.tracing import setup_tracing, instrument_fastapi
# from fastapi.staticfiles import StaticFiles
#
# logging.basicConfig(
#     level=logging.INFO,
#     format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
#     datefmt="%Y-%m-%d %H:%M:%S",
# )
# logger = logging.getLogger(__name__)
#
#
# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     settings = get_settings()
#     logger.info("研析通 v2.0 starting on %s:%s", settings.app.host, settings.app.port)
#     setup_tracing()
#     yield
#     logger.info("研析通 v2.0 shutting down")
#
#
# app = FastAPI(
#     title="研析通 v2.0",
#     description="面向高校科研全生命周期的多智能体系统 — KG×RAG 双引擎 + 分层多智能体协作 + 全链路可观测",
#     version="3.0.0",
#     lifespan=lifespan,
# )
#
# # CORS
# app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
#
# # Rate limiting
# app.add_middleware(RateLimitMiddleware, requests_per_minute=get_settings().rate_limit.requests_per_minute)
#
# # Input sanitization
# app.add_middleware(InputSanitizationMiddleware)
#
# # Routes
# app.include_router(router)
# # app.mount("/", StaticFiles(directory="templates", html=True), name="frontend")
#
# # Prometheus metrics endpoint
# @app.get("/metrics")
# async def metrics():
#     from src.observability.metrics import get_metrics_response
#     return get_metrics_response()
#
# # OpenTelemetry instrumentation
# instrument_fastapi(app)
#
# @app.get("/", include_in_schema=False)
# async def root():
#     from fastapi.responses import HTMLResponse
#     import os as _os
#     p = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "templates", "index.html")
#     if _os.path.exists(p):
#         return HTMLResponse(open(p, encoding="utf-8").read())
#     from starlette.responses import RedirectResponse
#     return RedirectResponse(url="/docs")
#
# if __name__ == "__main__":
#     import uvicorn
#     settings = get_settings()
#     uvicorn.run("src.main:app", host=settings.app.host, port=settings.app.port, reload=settings.app.debug, log_level=settings.app.log_level.lower())



"""研析通 v2.0 — Application entry point."""

import logging, os

# 必须在任何会读取环境变量的库被导入之前加载 .env：
# 例如 DASHSCOPE_API_KEY，以及 huggingface_hub 在导入时读取的 HF_ENDPOINT。
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("研析通 v2.0 starting on %s:%s", settings.app.host, settings.app.port)
    setup_tracing()
    yield
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