"""
Production AI Agent — Part 6 Final Project.

    uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
    python check_production_ready.py
"""
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth import verify_api_key
from app.config import settings
from app.cost_guard import check_budget, estimate_tokens, record_cost
from app.rate_limiter import check_rate_limit
from app.redis_client import r, redis_healthy
from utils.mock_llm import ask as llm_ask


# ── Structured logging (tiêu chí 10): mỗi dòng log là 1 JSON object ──
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def _setup_logger() -> logging.Logger:
    log = logging.getLogger("agent")
    log.setLevel(settings.log_level)
    log.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    log.addHandler(handler)
    log.propagate = False  # không đi qua root logger (tránh bị format lại dạng text)
    return log


logger = _setup_logger()


def log_event(event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": {"event": event, **fields}})


# ── Graceful shutdown (tiêu chí 11) ──
# uvicorn bắt SIGTERM: ngừng nhận request mới, chờ request đang chạy xong rồi mới
# chạy phần sau `yield` của lifespan. Mình không tự đăng ký signal handler để khỏi
# đè mất handler của uvicorn; phần dọn dẹp nằm trong lifespan.
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event("startup", environment=settings.environment, port=settings.port,
              rate_limit_per_minute=settings.rate_limit_per_minute,
              redis_ok=redis_healthy())
    yield
    log_event("shutdown", reason="SIGTERM/lifespan", note="in-flight requests finished")
    r.close()


app = FastAPI(title="Production AI Agent", version="1.0.0", lifespan=lifespan)
START_TIME = time.time()


@app.middleware("http")
async def access_log(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log_event("unhandled_error", logging.ERROR, path=request.url.path, request_id=request_id)
        response = JSONResponse(status_code=500, content={"detail": "Internal server error",
                                                          "request_id": request_id})
    response.headers["X-Request-ID"] = request_id
    log_event("request", method=request.method, path=request.url.path,
              status=response.status_code, ms=round((time.perf_counter() - started) * 1000, 1),
              request_id=request_id)
    return response


# ── Xử lý lỗi: mã lỗi rõ ràng, không lộ stack trace ──
@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "msg": e["msg"]}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Invalid request body", "errors": errors})


@app.exception_handler(redis.RedisError)
async def redis_error(request: Request, exc: redis.RedisError):
    log_event("redis_error", logging.ERROR, path=request.url.path, error=type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "Dependency unavailable, try again later"},
                        headers={"Retry-After": "5"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: str | None = Field(default=None, max_length=100)


@app.get("/health")
def health():
    """Liveness: luôn 200 khi process còn chạy, không phụ thuộc Redis."""
    return {"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1),
            "environment": settings.environment}


@app.get("/ready")
def ready():
    """Readiness: 503 khi Redis không kết nối được → load balancer ngừng gửi traffic."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Not ready: Redis unavailable")
    return {"ready": True}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    check_rate_limit(api_key)          # 429 khi vượt RATE_LIMIT_PER_MINUTE
    check_budget()                      # 402 khi hết budget

    started = time.perf_counter()
    answer = llm_ask(body.question)
    input_tokens, output_tokens = estimate_tokens(body.question), estimate_tokens(answer)
    cost = record_cost(input_tokens, output_tokens)

    log_event("llm_call", question_length=len(body.question), input_tokens=input_tokens,
              output_tokens=output_tokens, cost_usd=round(cost, 8),
              llm_ms=round((time.perf_counter() - started) * 1000, 1))
    return {"question": body.question, "answer": answer,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                      "cost_usd": round(cost, 8)}}
