# API middleware: RBAC + rate limiting.

import time
import logging
from typing import Optional
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.config import get_settings
from src.safety.guard import get_input_guard, get_output_guard

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter per client IP."""
    
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.rpm = requests_per_minute
        self._clients: dict[str, list[float]] = {}
    
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ("/api/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)
        
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        
        if client_ip not in self._clients:
            self._clients[client_ip] = []
        
        window = now - 60
        self._clients[client_ip] = [t for t in self._clients[client_ip] if t > window]
        
        if len(self._clients[client_ip]) >= self.rpm:
            logger.warning(f"Rate limit exceeded for {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please wait and try again."},
            )
        
        self._clients[client_ip].append(now)
        return await call_next(request)


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    """Sanitize user input through LLM Guard before processing."""
    
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body = await request.json()
                if isinstance(body, dict):
                    for key, value in body.items():
                        # 空串是合法可选字段（如 description=""），不构成注入风险，跳过
                        if isinstance(value, str) and value.strip():
                            guard = get_input_guard()
                            result = guard.check(value)
                            if not result.passed:
                                return JSONResponse(
                                    status_code=400,
                                    content={"detail": f"Input rejected: {result.blocked_reason}"},
                                )
            except Exception:
                pass
        
        response = await call_next(request)
        return response