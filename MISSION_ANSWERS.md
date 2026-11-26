# Day 14 Lab — Mission Answers

> Môi trường chạy: Windows 11 + **WSL2 Ubuntu 22.04**, Python 3.11.18 trong `.venv` (`os.name` = `posix`), Docker Desktop (server 29.9.3, Docker Compose v5.7.0).
> Mọi output dưới đây là output thật của lệnh đã chạy trên máy này (token/key đã được che).

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns tìm thấy trong `01-localhost-vs-production/develop/app.py`

| # | Anti-pattern | Vị trí code | Hậu quả |
|---|---|---|---|
| 1 | Hardcode secret: `OPENAI_API_KEY = "sk-hardcoded-..."` và `DATABASE_URL = "postgresql://admin:password123@..."` | `develop/app.py:17-18` | Push lên GitHub là lộ key và mật khẩu DB; muốn đổi key phải sửa code và deploy lại |
| 2 | Log ra secret: `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` | `develop/app.py:34` | Key nằm trong log của platform, ai đọc được log là có key (đã thấy thật trong log server, xem 1.2) |
| 3 | Dùng `print()` thay cho logging có cấu trúc | `develop/app.py:33,38` | Không có level, không có timestamp, không parse được bằng log aggregator |
| 4 | Config cứng trong code: `DEBUG = True`, `MAX_TOKENS = 500` | `develop/app.py:21-22` | Không đổi được giữa dev/staging/prod nếu không sửa code |
| 5 | Không có health check endpoint | cả file (gọi `GET /health` → **404**, xem 1.2) | Platform không biết agent còn sống không để restart |
| 6 | Bind `host="localhost"` | `develop/app.py:51` | Trong container, chỉ nhận kết nối từ chính container → bên ngoài không gọi được |
| 7 | Port cứng `port=8000`, không đọc `PORT` | `develop/app.py:52` | Railway/Render cấp port qua biến `PORT` → app nghe sai port, deploy fail |
| 8 | `reload=True` luôn bật | `develop/app.py:53` | Chế độ dev: tốn tài nguyên theo dõi file, không dùng cho production |
| 9 | Không xử lý SIGTERM / không có lifespan shutdown | cả file | Khi platform tắt container có thể cắt ngang request đang chạy |

### Exercise 1.2: Chạy bản develop

```text
$ cd 01-localhost-vs-production/develop && python app.py
$ curl -i -X POST "http://localhost:8000/ask?question=Hello"
HTTP/1.1 200 OK
server: uvicorn
content-type: application/json

{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}

$ curl -i http://localhost:8000/health
HTTP/1.1 404 Not Found

{"detail":"Not Found"}
```

Log server cùng lúc đó (chứng minh anti-pattern số 2, secret bị in ra log):

```text
INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)
[DEBUG] Got question: Hello
[DEBUG] Using key: sk-hardcoded-fake-key-never-do-this
[DEBUG] Response: Đây là câu trả lời từ AI agent (mock). ...
```

Quan sát: app **chạy được** nhưng chưa production-ready: lộ secret trong log, không có `/health`, chỉ nghe `localhost`.

### Exercise 1.3: So sánh develop và production

Chạy bản production (`cd ../production && python app.py`):

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":1.9,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T07:54:42.868256+00:00"}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đi nhé.","model":"gpt-4o-mini"}
```

Khi gửi SIGTERM, log server cho thấy uvicorn tắt êm: `Shutting down → Waiting for application shutdown → Application shutdown complete → Finished server process`.

| Feature | Develop | Production | Tại sao quan trọng? |
|---|---|---|---|
| Config | Hardcode trong code (`app.py:17-22`) | Đọc env qua `config.py` (`Settings` + `os.getenv`, `config.py:17-35`) | Một image chạy được ở mọi môi trường, chỉ đổi biến môi trường; secret không nằm trong git |
| Secrets | `sk-...` và mật khẩu DB trong code, còn in ra log | `OPENAI_API_KEY`, `AGENT_API_KEY` từ env; `validate()` báo lỗi nếu thiếu key ở production (`config.py:37-46`) | Lộ key = mất tiền / mất dữ liệu; fail-fast giúp phát hiện cấu hình thiếu ngay khi khởi động |
| Host / Port | `localhost:8000` cố định | `HOST=0.0.0.0`, `PORT` từ env (`config.py:17-18`) | Container cần bind `0.0.0.0`; cloud platform tự cấp `PORT` |
| Health check | Không có (`/health` → 404) | `GET /health` (liveness) + `GET /ready` (readiness, 503 khi chưa sẵn sàng) | Platform biết khi nào restart container, load balancer biết khi nào được gửi traffic |
| Logging | `print()` | `logging` với format JSON, không log secret (`app.py:28-31, 111-115`) | Log có cấu trúc thì search/lọc/alert được trong Loki, Datadog...; không lộ secret |
| Shutdown | Đột ngột | `lifespan` có phần shutdown + handler SIGTERM (`app.py:39-67, 180-189`) | Hoàn thành request đang xử lý, đóng kết nối trước khi tắt, không mất dữ liệu |
| Reload / debug | `reload=True` luôn bật | `reload=settings.debug` (chỉ bật khi `DEBUG=true`) | Production không tốn tài nguyên theo dõi file, không chạy chế độ debug |
| CORS | Không cấu hình | `ALLOWED_ORIGINS` từ env | Chỉ frontend được phép mới gọi được API từ trình duyệt |

**Quan sát thêm khi chạy thật:** log của bản production **không hiện dòng JSON nào** (chỉ thấy log của uvicorn). Lý do: `config.py:45` gọi `logging.warning(...)` lúc import, việc này tự cấu hình root logger trước khi `app.py:28` gọi `logging.basicConfig(...)`, nên `basicConfig` không còn tác dụng và các log INFO dạng JSON bị bỏ qua. Bài học cho Part 6: phải tự gắn handler/formatter JSON cho logger của app thay vì chỉ dựa vào `basicConfig`.

---

## Part 2: Docker

### Exercise 2.1: Câu hỏi về `02-docker/develop/Dockerfile`

1. **Base image:** `python:3.11`, bản Python đầy đủ (Debian + compiler + nhiều tool), nên image rất nặng.
2. **Working directory:** `/app` (`WORKDIR /app`); các lệnh `COPY`, `RUN`, `CMD` sau đó chạy tương đối từ đây.
3. **Vì sao `COPY requirements.txt` trước:** Docker cache theo từng layer. Requirements ít thay đổi hơn code, nên copy và `pip install` trước thì khi chỉ sửa `app.py`, layer cài dependencies vẫn dùng cache và build lại chỉ mất vài giây. Nếu `COPY . .` trước thì mọi lần sửa code đều làm mất cache và phải cài lại toàn bộ thư viện.
4. **CMD và ENTRYPOINT:** `CMD` là lệnh/tham số mặc định, bị ghi đè khi chạy `docker run image <lệnh khác>`. `ENTRYPOINT` là chương trình chính cố định của container; tham số của `docker run` được nối thêm vào sau nó (muốn đổi phải dùng `--entrypoint`). Hay dùng kết hợp: `ENTRYPOINT ["uvicorn"]` + `CMD ["main:app", "--port", "8000"]`.

### Exercise 2.2: Build và chạy bản develop (build từ gốc repo)

```text
$ docker build -f 02-docker/develop/Dockerfile -t my-agent:develop .
$ docker run -d --rm --name p2dev -p 8000:8000 my-agent:develop
$ curl -i -X POST "http://localhost:8000/ask?question=What%20is%20Docker%3F"
HTTP/1.1 200 OK
{"answer":"Container là cách đóng gói app để chạy ở mọi nơi. Build once, run anywhere!"}

$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":4.0,"container":true}
```

### Exercise 2.3: Multi-stage build và so sánh kích thước

- **Stage 1 (`builder`, `python:3.11-slim`):** cài `gcc`, `libpq-dev`, rồi `pip install --user -r requirements.txt` vào `/root/.local`. Stage này có compiler và apt cache nhưng **không được deploy**.
- **Stage 2 (`runtime`, `python:3.11-slim`):** tạo user non-root `appuser`, chỉ `COPY --from=builder /root/.local` (thư viện đã cài, layer khoảng 40.8 MB) + `main.py` + `utils/mock_llm.py`, có `HEALTHCHECK`, chạy `uvicorn --workers 2`.
- **Vì sao nhỏ hơn:** base `slim` thay cho bản full; compiler, header và apt cache chỉ nằm ở stage builder rồi bị bỏ lại; image cuối chỉ chứa runtime + package + code.

```text
$ docker build -f 02-docker/production/Dockerfile -t my-agent:advanced .
$ docker images my-agent
IMAGE               ID             DISK USAGE   CONTENT SIZE
my-agent:advanced   5eed06689352        240MB         57.4MB
my-agent:develop    13f59cf1fa43       1.67GB          426MB

$ docker image inspect my-agent:develop my-agent:advanced --format '{{index .RepoTags 0}} {{.Size}}'
my-agent:develop 1667796284
my-agent:advanced 239819422
```

- Develop: **1667.8 MB** (≈ 1.67 GB)
- Production: **239.8 MB**
- Chênh lệch: (1667.8 − 239.8) / 1667.8 ≈ **85.6% nhỏ hơn** (image production chỉ bằng ≈ 14.4% image develop).

Chạy thử image production: `GET /health` → 200, `POST /ask` (JSON) → 200, `docker exec p2adv whoami` → `appuser` (không chạy bằng root).

### Exercise 2.4: Docker Compose stack (`02-docker/production/`)

Sơ đồ (đọc từ `docker-compose.yml` và `nginx/nginx.conf`):

```text
                 host :8088 (*)
                       │
                       ▼
               ┌──────────────┐   network "internal" (bridge)
 Client ─────► │ nginx:alpine │   - reverse proxy + rate limit 10 r/s/IP
               └──────┬───────┘   - thêm security headers
                      │ proxy_pass http://agent_backend (agent:8000)
                      ▼
               ┌──────────────┐  ENV: REDIS_URL=redis://redis:6379/0
               │    agent     │       QDRANT_URL=http://qdrant:6333
               │ (FastAPI,    │  không publish port ra host
               │  2 workers)  │
               └───┬──────┬───┘
     depends_on    │      │   depends_on (service_healthy)
                   ▼      ▼
         ┌───────────┐  ┌──────────────┐
         │ redis:7   │  │ qdrant v1.9  │
         │ :6379     │  │ :6333        │
         │ vol redis_data │ vol qdrant_data │
         └───────────┘  └──────────────┘
```

Có 4 service được start: `agent`, `redis`, `qdrant`, `nginx`. Chỉ Nginx publish port ra host. Các service nói chuyện với nhau qua network `internal` bằng tên service (DNS nội bộ của Docker: `agent:8000`, `redis:6379`, `qdrant:6333`). Agent chỉ start khi Redis và Qdrant đã healthy.

(*) Trên máy này cổng 80 của Windows đang bị dịch vụ hệ thống HTTP.sys (PID 4) giữ, nên `nginx` báo `ports are not available: 0.0.0.0:80`. Mình không sửa `docker-compose.yml` của đề mà dùng thêm một file override ngoài repo (`ports: !override ["8088:80"]`) để map Nginx ra cổng 8088.

```text
$ ls -la .env.local          # đã tạo bằng touch, để trống
-rwxrwxrwx 1 limdim limdim 0 Nov 26 14:19 .env.local
$ docker compose -p k5-day14-docker -f docker-compose.yml -f <override 8088:80> up -d --build
$ docker compose -p k5-day14-docker ps
SERVICE   IMAGE                   STATUS                    PORTS
agent     k5-day14-docker-agent   Up 6 seconds (healthy)    8000/tcp
nginx     nginx:alpine            Up 6 seconds              0.0.0.0:8088->80/tcp, [::]:8088->80/tcp
qdrant    qdrant/qdrant:v1.9.0    Up 17 seconds (healthy)   6333-6334/tcp
redis     redis:7-alpine          Up 17 seconds (healthy)   6379/tcp

$ curl -i http://localhost:8088/health
HTTP/1.1 200 OK
Server: nginx
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block

{"status":"ok","uptime_seconds":5.7,"version":"2.0.0","timestamp":"2026-11-26T07:59:09.969289"}

$ curl -i -X POST http://localhost:8088/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
HTTP/1.1 200 OK
Server: nginx
{"answer":"Tôi là AI agent được deploy lên cloud. Câu hỏi của bạn đã được nhận."}

$ docker compose -p k5-day14-docker down -v
```

Header `Server: nginx` và các security header chứng minh request đi qua Nginx rồi mới tới agent. Log agent cho thấy request đến từ IP nội bộ của Nginx (`172.22.0.5`).

---

## Part 3: Cloud Deployment

_(điền sau CP6: deploy agent Part 6)_

---

## Part 4: API Security

### Exercise 4.1: API key authentication (`04-api-gateway/develop/`)

- **Key được kiểm tra ở đâu:** dependency `verify_api_key()` (`develop/app.py:39-54`) đọc header `X-API-Key` qua `APIKeyHeader(name="X-API-Key", auto_error=False)` và được gắn vào `/ask` bằng `Depends(verify_api_key)` (`app.py:70`).
- **Sai key thì sao:** thiếu header → **401** `Missing API key`; có header nhưng sai → **403** `Invalid API key`.
- **Rotate key:** key đọc từ biến môi trường `AGENT_API_KEY` (`app.py:35`), nên chỉ cần đặt giá trị mới trên platform rồi restart/redeploy, không sửa code. Muốn không bị gián đoạn thì cho phép tạm thời 2 key (cũ + mới), chuyển client sang key mới rồi bỏ key cũ.
- Lưu ý: app còn in key ra màn hình lúc khởi động (`print(f"Test: curl -H 'X-API-Key: {API_KEY}' ...")`), đây cũng là một kiểu lộ secret qua log.

Chạy bằng `AGENT_API_KEY=secret-key-123 python app.py`:

```text
$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 401 Unauthorized
{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: wrong-key" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 403 Forbidden
{"detail":"Invalid API key."}

$ curl -i -X POST http://localhost:8000/ask -H "X-API-Key: ***" -H "Content-Type: application/json" -d '{"question":"Hello"}'
HTTP/1.1 200 OK
{"question":"Hello","answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
```

Sau đó dừng bản develop để trả cổng 8000.

### Exercise 4.2: JWT (`04-api-gateway/production/`)

Luồng JWT (`auth.py`): `POST /auth/token` kiểm tra username/password → `create_token()` ký payload `{sub, role, iat, exp}` bằng HS256 với `JWT_SECRET` (hết hạn sau 60 phút) → client gửi `Authorization: Bearer <token>` → `verify_token()` kiểm chữ ký và hạn, lấy ra `username` và `role` mà không cần tra database.

```text
$ curl -i -X POST http://localhost:8000/auth/token -H "Content-Type: application/json" -d '{"username":"student","password":"demo123"}'
HTTP/1.1 200 OK
{"access_token":"<TOKEN_DA_CHE>","token_type":"bearer","expires_in_minutes":60,"hint":"Include in header: Authorization: Bearer <TOKEN_DA_CHE>..."}

$ curl -i -X POST http://localhost:8000/ask -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer
{"detail":"Authentication required. Include: Authorization: Bearer <token>"}

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Explain JWT"}'
HTTP/1.1 200 OK
{"question":"Explain JWT","answer":"Đây là câu trả lời từ AI agent (mock). ...","usage":{"requests_remaining":9,"budget_remaining_usd":2.1e-05}}
```

### Exercise 4.3: Rate limiting

- **Thuật toán:** sliding window log, mỗi user một `deque` timestamp; bỏ các timestamp cũ hơn 60 giây rồi đếm (`rate_limiter.py:35-45`).
- **Giới hạn:** user thường **10 request/phút** (`rate_limiter_user`), admin 100 request/phút (`rate_limiter_admin`) (`rate_limiter.py:86-87`).
- **Bypass cho admin:** `app.py:141` chọn limiter theo role trong JWT: `role == "admin"` (tài khoản `teacher`) dùng limiter 100/phút. Muốn bỏ hẳn giới hạn thì bỏ qua bước `limiter.check()` khi role là admin.
- **Hạn chế:** state nằm trong RAM của process, nên chạy nhiều instance/worker thì mỗi nơi đếm riêng và restart là mất. Production cần Redis (Part 6 làm điều này).

Vòng lặp 20 request bằng token của `student` (lần gọi ở 4.2 đã dùng 1 lượt):

```text
$ for i in $(seq 1 20); do curl -s -o /dev/null -w "req $i -> %{http_code}\n" -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "{\"question\":\"Test $i\"}"; done
req 1 -> 200
req 2 -> 200
req 3 -> 200
req 4 -> 200
req 5 -> 200
req 6 -> 200
req 7 -> 200
req 8 -> 200
req 9 -> 200
req 10 -> 429
...
req 20 -> 429

$ curl -i -X POST http://localhost:8000/ask -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"question":"Test 21"}'
HTTP/1.1 429 Too Many Requests
x-ratelimit-limit: 10
x-ratelimit-remaining: 0
x-ratelimit-reset: 1795680055
retry-after: 59
{"detail":{"error":"Rate limit exceeded","limit":10,"window_seconds":60,"retry_after_seconds":59}}
```

### Exercise 4.4: Cost guard

Cách làm trong `04-api-gateway/production/cost_guard.py`:

1. **Trước khi gọi LLM:** `check_budget(user)`. Nếu tổng chi phí toàn hệ thống trong ngày ≥ `$10` → **503**; nếu chi phí của user trong ngày ≥ `$1` → **402 Payment Required**; ≥ 80% thì ghi log cảnh báo.
2. **Sau khi gọi LLM:** `record_usage(user, input_tokens, output_tokens)` cộng token và tính tiền theo giá `$0.15/1M input` và `$0.60/1M output`.
3. Bản ghi được reset theo ngày (`day = time.strftime("%Y-%m-%d")`).

```text
$ curl -s http://localhost:8000/me/usage -H "Authorization: Bearer $TOKEN"
{"user_id":"student","date":"2026-11-26","requests":10,"input_tokens":40,"output_tokens":308,"cost_usd":0.000191,"budget_usd":1.0,"budget_remaining_usd":0.999809,"budget_used_pct":0.0}
```

**Rate limit và cost guard khác nhau thế nào, vì sao cần cả hai:**
- **Rate limit bảo vệ tốc độ:** giới hạn *số request trong một khoảng thời gian* để chống spam, brute force và một client làm nghẽn server. Nó không quan tâm request đắt hay rẻ.
- **Cost guard bảo vệ tiền:** giới hạn *tổng chi phí token* trong ngày/tháng. Một user gửi ít request nhưng mỗi câu rất dài (nhiều token) vẫn lọt qua rate limit nhưng có thể đốt hết budget; ngược lại rate limit chặn được bão request rẻ mà cost guard chưa kịp thấy.
- Vì vậy cần cả hai lớp. Bản demo này lưu trong RAM; bản production thật phải lưu trong Redis (key theo user + ngày/tháng, `INCRBYFLOAT`, `EXPIRE`) để nhiều instance dùng chung và không mất khi restart. Đó chính là cách mình làm ở Part 6.

---

## Part 5: Scaling & Reliability

### Exercise 5.1: Health và readiness (`05-scaling-reliability/develop/`)

- `GET /health` (**liveness**): process còn sống không, kèm uptime và % RAM (psutil). Lỗi thì platform **restart** container.
- `GET /ready` (**readiness**): đã sẵn sàng nhận traffic chưa (đã khởi động xong, chưa vào giai đoạn shutdown). Trả 503 thì load balancer **ngừng gửi traffic** nhưng không restart.

```text
$ curl -i http://localhost:8000/health
HTTP/1.1 200 OK
{"status":"ok","uptime_seconds":3.0,"version":"1.0.0","environment":"development","timestamp":"2026-11-26T08:00:24.452433+00:00","checks":{"memory":{"status":"ok","used_percent":17.5}}}

$ curl -i http://localhost:8000/ready
HTTP/1.1 200 OK
{"ready":true,"in_flight_requests":1}
```

### Exercise 5.2: Graceful shutdown

Gửi một request `/ask` (mock LLM mất khoảng 3 giây) rồi `kill -TERM` process ngay 0.1 giây sau:

```text
$ curl -s -w "\nHTTP %{http_code} trong %{time_total}s\n" -X POST "http://localhost:8000/ask?question=Long%20task" & sleep 0.1; kill -TERM $PID
{"answer":"Đây là câu trả lời từ AI agent (mock). Trong production, đây sẽ là response từ OpenAI/Anthropic."}
HTTP 200 trong 3.049141s
process đã tự thoát, exit code 0
```

Log server:

```text
INFO:     Shutting down
INFO:     127.0.0.1:54872 - "POST /ask?question=Long%20task HTTP/1.1" 200 OK
INFO:     Waiting for application shutdown.
2026-11-26 15:00:27,617 INFO 🔄 Graceful shutdown initiated...
2026-11-26 15:00:27,618 INFO ✅ Shutdown complete
INFO:     Application shutdown complete.
INFO:     Finished server process [13701]
```

Kết luận: nhận SIGTERM, uvicorn ngừng nhận request mới nhưng **chờ request đang chạy trả xong (200)**, sau đó chạy phần shutdown của `lifespan` (chờ `_in_flight_requests` về 0, tối đa 30 giây) rồi mới thoát với exit code 0.

### Exercise 5.3: Stateless design

`05-scaling-reliability/production/app.py` không giữ conversation trong dict của process mà lưu session vào Redis: `save_session()` → `SETEX session:<id>` TTL 3600 giây, `load_session()` → `GET`, `append_to_history()` đọc, thêm tin nhắn rồi ghi lại. Mỗi instance có `INSTANCE_ID` riêng và trả về `served_by` để thấy request rơi vào instance nào. Nếu để state trong RAM, mỗi instance có một bản riêng: request sau rơi vào instance khác thì mất history, và restart hay scale down cũng mất.

### Exercise 5.4: Load balancing với 3 agent

```text
$ cd 05-scaling-reliability/production
$ docker compose -p k5-day14-scaling up -d --build --scale agent=3
$ docker compose -p k5-day14-scaling ps
NAME                       STATUS                    PORTS
k5-day14-scaling-agent-1   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-2   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-agent-3   Up 6 seconds (healthy)    8000/tcp
k5-day14-scaling-nginx-1   Up 6 seconds              0.0.0.0:8080->80/tcp, [::]:8080->80/tcp
k5-day14-scaling-redis-1   Up 17 seconds (healthy)   6379/tcp
```

10 request `POST /chat` qua Nginx (cổng 8080), đọc header `X-Served-By` và trường `served_by`:

```text
req 1: X-Served-By: 172.22.0.5:8000   served_by = instance-a11094
req 2: X-Served-By: 172.22.0.4:8000   served_by = instance-de397f
req 3: X-Served-By: 172.22.0.3:8000   served_by = instance-fa2b2f
req 4: X-Served-By: 172.22.0.5:8000   served_by = instance-a11094
req 5: X-Served-By: 172.22.0.4:8000   served_by = instance-de397f
req 6: X-Served-By: 172.22.0.3:8000   served_by = instance-fa2b2f
req 7: X-Served-By: 172.22.0.5:8000   served_by = instance-a11094
req 8: X-Served-By: 172.22.0.4:8000   served_by = instance-de397f
req 9: X-Served-By: 172.22.0.3:8000   served_by = instance-fa2b2f
req 10: X-Served-By: 172.22.0.5:8000  served_by = instance-a11094
```

Nginx chia **round-robin** đều cho 3 instance. Trong `nginx.conf`, `upstream` chỉ khai báo `server agent:8000`, nhưng Docker DNS trả về IP của cả 3 container nên Nginx tự có 3 backend.

### Exercise 5.5: Test stateless

```text
$ python test_stateless.py
============================================================
Stateless Scaling Demo
============================================================

Session ID: 2e0e2cf6-eea2-4673-9731-f59c3f234b7a

Request 1: [instance-a11094]
  Q: What is Docker?
Request 2: [instance-de397f]
  Q: Why do we need containers?
Request 3: [instance-fa2b2f]
  Q: What is Kubernetes?
Request 4: [instance-a11094]
  Q: How does load balancing work?
Request 5: [instance-de397f]
  Q: What is Redis used for?
------------------------------------------------------------
Total requests: 5
Instances used: {'instance-de397f', 'instance-fa2b2f', 'instance-a11094'}
✅ All requests served despite different instances!

--- Conversation History ---
Total messages: 10
  [user]: What is Docker?...
  [assistant]: Container là cách đóng gói app để chạy ở mọi nơi. Build once...
  [user]: Why do we need containers?...
  ...
  [user]: What is Redis used for?...
  [assistant]: Agent đang hoạt động tốt! (mock response) Hỏi thêm câu hỏi đ...

✅ Session history preserved across all instances via Redis!
```

Thử thêm (script không tự tắt instance nào): dừng một agent rồi đọc lại history của cùng session 3 lần:

```text
$ docker stop k5-day14-scaling-agent-1
history count = 10
history count = 10
history count = 10
$ docker compose -p k5-day14-scaling down
```

**Giải thích, vai trò của Redis:** 5 câu hỏi của *cùng một session* lần lượt rơi vào cả 3 instance khác nhau, nhưng history vẫn đủ 10 tin nhắn (5 user + 5 assistant) theo đúng thứ tự. Lý do là không instance nào giữ history trong RAM: mỗi request đọc session từ Redis, thêm tin nhắn rồi ghi lại, nên instance nào phục vụ cũng thấy cùng một dữ liệu. Khi tắt một instance, Nginx chuyển traffic sang 2 instance còn lại và history vẫn còn nguyên vì nó nằm trong Redis chứ không nằm trong container đã chết. Redis trở thành nơi giữ state duy nhất, nhờ đó agent scale ngang (thêm/bớt instance, rolling deploy) được mà không mất dữ liệu người dùng.

---

## Part 6: Final Project

_(điền sau CP5: output `check_production_ready.py`)_

---

## AI log

_(cập nhật ở cuối bài)_
