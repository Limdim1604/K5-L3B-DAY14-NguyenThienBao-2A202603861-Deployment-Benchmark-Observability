"""
Kết nối Redis dùng chung cho rate limiter và cost guard.

  • Đọc REDIS_URL từ config
  • App vẫn KHỞI ĐỘNG ĐƯỢC khi Redis chưa sẵn sàng: redis.from_url() chỉ tạo
    connection pool, chưa mở kết nối nào nên không ping lúc import
  • redis_healthy() dùng cho GET /ready: Redis không kết nối được → False
"""
import redis

from app.config import settings

# Timeout ngắn để /ready trả 503 nhanh khi Redis chết, không treo request
r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=2,
    retry_on_timeout=False,
    health_check_interval=30,
)


def redis_healthy() -> bool:
    try:
        return bool(r.ping())
    except redis.RedisError:
        return False
