# Deployment Information

## Public URL

https://day14-agent-8ajm.onrender.com

## Platform

**Render** (Blueprint), deploy chính agent Part 6 trong `06-lab-complete/`:

- Blueprint path: `06-lab-complete/render.yaml` (repo public, nhánh `main`, commit `4a499db`)
- Web service `day14-agent`: runtime Docker (multi-stage `06-lab-complete/Dockerfile`), plan Free, region Oregon, health check `/health`
- Key Value `day14-redis` (Valkey 8, plan Free): Redis cho rate limit + cost guard, nối vào agent qua `REDIS_URL` (`fromService ... connectionString`)

> Gói Free của Render tự ngủ khi không có traffic. Request đầu tiên sau một lúc không dùng có thể mất khoảng 50 giây để service thức dậy; các request sau trả lời bình thường.

## Test Commands

Đặt biến (key lấy trong Render Dashboard → `day14-agent` → Environment → `AGENT_API_KEY`, **không ghi vào repo**):

```bash
export BASE=https://day14-agent-8ajm.onrender.com
export AGENT_API_KEY=<key trên Render>
```

4 lệnh tự kiểm của CP6 (mỗi lệnh chỉ in mã HTTP):

```bash
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"     # 200
curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"      # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'   # 401
curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'   # 200
```

Kết quả thật (26/11/2026, chạy từ WSL2, key đã che):

```text
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/health"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' "$BASE/ready"
200
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -d '{"question":"Hello"}'
401
$ curl -sS -o /dev/null -w '%{http_code}\n' -X POST "$BASE/ask" -H 'Content-Type: application/json' -H "X-API-Key: $AGENT_API_KEY" -d '{"question":"Hello"}'
200

$ curl -s "$BASE/health"
{"status":"ok","uptime_seconds":408.1,"environment":"production"}
$ curl -s -X POST "$BASE/ask" -H "X-API-Key: ***" -H 'Content-Type: application/json' -d '{"question":"What is deployment?"}'
{"question":"What is deployment?","answer":"Deployment là quá trình đưa code từ máy bạn lên server để người khác dùng được.","usage":{"input_tokens":5,"output_tokens":20,"cost_usd":1.275e-05}}
```

Rate limit trên cloud (`RATE_LIMIT_PER_MINUTE=10`, đã dùng 2 lượt ở trên). State nằm trong Redis của Render:

```text
req 1 -> 200 ... req 8 -> 200
req 9 -> 429
req 10 -> 429
req 11 -> 429
req 12 -> 429
```

Script chấm kiểm tra URL public:

```text
$ python check_production_ready.py --static-only --public-url https://day14-agent-8ajm.onrender.com
✅ 12. Public URL ready (Railway / Render config)
     ✅ Public URL: GET https://day14-agent-8ajm.onrender.com/health → 200
```

## Environment Variables Set

Đặt trên Render (khai báo trong `render.yaml`, secret không ghi giá trị trong repo):

| Biến | Giá trị / nguồn |
|---|---|
| `AGENT_API_KEY` | `generateValue: true`: Render tự sinh chuỗi ngẫu nhiên 44 ký tự; chỉ xem trong Dashboard |
| `REDIS_URL` | `fromService` → Key Value `day14-redis`, `connectionString` |
| `ENVIRONMENT` | `production` (app fail-fast nếu thiếu `AGENT_API_KEY` ở môi trường này) |
| `RATE_LIMIT_PER_MINUTE` | `10` |
| `DAILY_BUDGET_USD` | `1.0` |
| `MONTHLY_BUDGET_USD` | `10.0` |
| `PORT` | Render tự cấp; `CMD` trong Dockerfile nghe `${PORT:-8000}` |

## Screenshots

- [Deployment dashboard](screenshots/dashboard.png): Render Overview, `day14-agent` Deployed + `day14-redis` Available
- [Service running](screenshots/running.png): web service `day14-agent` **Live**, Docker, Free, Blueprint managed, deploy commit `4a499db`
- [Test results](screenshots/test.png): output thật của 4 lệnh CP6 + rate limit + check `--public-url` (render từ log lệnh, key đã che)
- Thêm: [App logs trên Render](screenshots/render-logs.png): mỗi dòng log của app là JSON
