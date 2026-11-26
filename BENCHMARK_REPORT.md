# Benchmark & Observability Report — Day 14

> Mọi con số dưới đây lấy từ file JSON trong `reports/`, CSV trong `benchmark-results/`, ảnh trong `screenshots/` và output lệnh đã chạy. Không có số ước lượng.

**Họ tên:** Nguyễn Thiên Bảo — 2A202603861
**Ngày chạy:** 26/11/2026 (khoảng 15:10 → 15:55, GMT+7)
**Môi trường:** Laptop Windows 11, 16 luồng CPU, WSL2 Ubuntu 22.04 (7.6 GB RAM cho WSL), Python 3.11.18, Docker Desktop (engine 29.9.3, Compose v5.7.0). Agent develop chạy trực tiếp trong WSL; stack production chạy bằng `docker compose -p k5-day14-observability` (mỗi agent giới hạn 0.5 CPU / 256 MB).

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < **500 ms** (đo bằng `load_test.py`); alert production cảnh báo khi > 1 s kéo dài 2 phút | Mock LLM mất khoảng 120–180 ms + 20 ms retrieval, nên p50 khoảng 190 ms. Đặt 500 ms là gấp khoảng 2.5 lần p50 bình thường: đủ chỗ cho jitter, nhưng 5% request chậm bất thường (+1.5 s) sẽ làm vỡ SLO ngay. Đặt theo p95 vì người dùng cảm nhận theo request chậm, không theo trung bình |
| Error rate `/ask` | < **2 %** (load test); alert khi > 5% trong 1 phút | Upstream LLM tự có khoảng 1% lỗi (`MOCK_ERROR_RATE=0.01`), nên 2% là "bình thường cộng biên an toàn". Alert 5% để tránh báo động giả do dao động ngắn |
| Accuracy trên golden dataset | ≥ **85 %** để được merge (CI); bản release thực tế đạt **100%** (23/23) và không có regression | 85% cho phép vài câu khó chưa có tài liệu trong knowledge base, nhưng chặn được thay đổi làm agent tệ hẳn (v1 chỉ 4.3%). Ngoài ngưỡng tuyệt đối, gate còn so `--baseline` để chặn mọi câu từng đúng mà nay sai |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2, 300 request mỗi mức)

Nguồn: `reports/load_v2_c1.json`, `load_v2_c10.json`, `load_v2_c50.json`, `load_v2_c100.json`.

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 4.48 | 190.7 | 218.6 | 1672.6 | 1.33% (4×503) | ✅ PASS |
| 10 | 38.97 | 192.6 | 222.5 | 1698.8 | 1.33% (4×503) | ✅ PASS |
| 50 | 104.33 | 196.8 | 253.2 | 1708.1 | 0.67% (2×503) | ✅ PASS |
| 100 | 84.93 | 291.3 | **1764.6** | 1958.1 | 1.33% (4×503) | ❌ FAIL (p95 > 500 ms, exit code 1) |

**Nhận xét:** RPS tăng gần tuyến tính từ 1 lên 10 (4.48 → 38.97, gấp khoảng 8.7 lần) và lên 50 (104.33). Lý do là mỗi request chủ yếu *chờ* mock LLM (`asyncio.sleep`), nên một process uvicorn xử lý song song được nhiều request mà latency gần như không đổi (p50 khoảng 190–197 ms). Lên 100 thì **RPS giảm** còn 84.93, p50 tăng lên 291 ms và p95 nhảy lên 1764.6 ms, tức vi phạm SLO. Agent chỉ chạy 1 process Python, một event loop trên một lõi CPU. Ở 100 request song song, phần việc CPU (parse JSON, Pydantic, middleware đo metric, ghi log JSON), cộng với việc load generator chạy cùng máy, bắt đầu tạo hàng đợi. Request phải xếp hàng trước khi được xử lý nên latency tăng còn throughput không tăng thêm. Điểm gãy nằm giữa 50 và 100 concurrent; muốn phục vụ nhiều hơn thì phải scale ngang (nhiều process hoặc instance) thay vì đẩy concurrency vào một process.

### 2.2 Tail latency (v2, 300 request, concurrency 20, khởi động lại v2 cho mỗi mức)

Nguồn: `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`.

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 188.0 | 216.8 | 220.2 |
| 0.03 (mặc định) | 239.7 | 220.0 | 1696.3 |
| 0.1 | 332.1 | **1687.7** | 1710.6 |

**Nhận xét:**
- Với 3% request chậm, **avg chỉ tăng khoảng 52 ms** (188 → 240) và p95 gần như không đổi, nhưng **p99 nhảy từ 220 ms lên 1696 ms**.
- Với 10% request chậm (lớn hơn 5%), chính p95 rơi vào nhóm chậm: **p95 = 1688 ms, vi phạm SLO**, trong khi avg mới tăng khoảng 144 ms so với không có request chậm.
- Percentile cao (p99, rồi p95) nhạy nhất vì nó chỉ ra trải nghiệm của nhóm user tệ nhất. Average là trung bình cộng nên vài request chậm 1.7 s bị "pha loãng" bởi hàng trăm request 190 ms.
- Đặt SLO theo average sẽ báo "ổn" trong khi 3–10% người dùng phải chờ gần 2 giây. Vì vậy SLO đặt theo p95/p99.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`, golden dataset 23 câu)

Nguồn: `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json`.

| Chỉ số | Agent v1 | Agent v2 | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 91.3% (21/23) | **100% (23/23)** |
| Avg tokens / câu | 56.8 | 109.0 | 115.8 |
| Tổng cost (USD) | 0.000400 | 0.000759 | 0.000809 |
| Cost / câu đúng (USD) | 0.00040035 | 0.00003616 | 0.00003518 |
| Quality gate | ❌ FAIL (≥ 80%) | ✅ PASS (≥ 80%, 0 regression so với v1) | ✅ PASS (≥ 100%, 0 regression so với v2) |

**Những câu v2 trả lời sai ban đầu và cách sửa:**

| ID | Câu hỏi | Keyword bị thiếu | Đã sửa gì |
|---|---|---|---|
| security-02 | JWT khác gì API key trong xác thực? | `token`, `hết hạn`, `chữ ký` | Không có tài liệu nào khớp nên retrieval rỗng và agent trả lời chung chung. Thêm entry `(["jwt", "api key", "xác thực"], ...)` vào `KNOWLEDGE_BASE`: API key là chuỗi cố định không tự hết hạn; JWT là token có chữ ký số, chứa user và thời điểm hết hạn |
| scaling-03 | Kubernetes HPA scale dựa trên những chỉ số nào? | `cpu`, `memory`, `custom metrics` | Thêm entry `(["hpa", "kubernetes", "autoscal"], ...)`: HPA scale số pod dựa trên CPU, memory hoặc custom metrics (RPS, độ dài hàng đợi) |

Trước khi thêm, mình kiểm tra (bằng `grep` trên golden dataset) rằng các keyword mới chỉ xuất hiện trong đúng 2 câu này, nên không "cướp" retrieval của câu khác. Kết quả: `Câu được cải thiện: 2`, `Câu bị regression: 0`. File `golden_dataset.jsonl` giữ nguyên (`git diff` trống). Phần sửa cũng được chép sang `production/utils/mock_llm.py` để stack Docker dùng cùng knowledge base.

Lệnh cuối: `python eval/run_eval.py --url http://localhost:8002 --min-accuracy 1.0 --baseline reports/eval_v2.json --out reports/eval_v2_fixed.json` in `Quality gate (accuracy ≥ 100%, không regression): ✅ PASS`, exit code 0.

**Quality gate trong CI:** `.github/workflows/quality-gate.yml` chạy trên mỗi pull request. Job bật agent v2 (`MOCK_ERROR_RATE=0` để eval ổn định) rồi chạy `python eval/run_eval.py --min-accuracy 0.85`. `run_eval.py` trả **exit code 1** khi accuracy dưới ngưỡng hoặc có regression, nên step fail và cả job fail. Bật branch protection "Require status checks to pass" thì PR đó không merge được. Đã kiểm tra hành vi exit code bằng chính lần eval v1 ở trên: accuracy 4.3% cho `(exit code 1)`.

**Trade-off chất lượng / chi phí / latency:**
- v2 thêm bước retrieval nên prompt dài gần gấp đôi (109 so với 56.8 token mỗi câu), tổng cost gấp khoảng 1.9 lần, p95 eval tăng từ 202 lên 260 ms (thêm khoảng 20 ms retrieval và prompt dài hơn).
- Đổi lại accuracy tăng từ 4.3% lên 91.3%, nên **cost trên mỗi câu đúng rẻ hơn khoảng 11 lần** (0.00040035 so với 0.00003616 USD).
- So hai phiên bản theo "cost / câu" sẽ kết luận sai rằng v1 rẻ hơn; "cost / câu đúng" mới phản ánh tiền bỏ ra để có câu trả lời dùng được. Sửa KB tăng thêm khoảng 6.8 token mỗi câu nhưng cost / câu đúng còn giảm tiếp.

---

## 4. Observability stack (production)

Stack: `docker compose -p k5-day14-observability up -d --build` (2 agent + Nginx + Prometheus + Grafana + Jaeger + Locust).

- Unit test alert rules: `promtool test rules /p/alert_rules_test.yml` cho kết quả `SUCCESS`.
- **Sự cố khi bật stack:** agent restart liên tục với lỗi `ModuleNotFoundError: No module named 'uvicorn'`. Nguyên nhân là `production/Dockerfile` tạo user `agent` với home `/app` (`useradd -d /app`), nên Python tìm user site ở `/app/.local`, trong khi package được copy vào `/home/agent/.local`. Sửa bằng cách thêm `ENV PYTHONUSERBASE=/home/agent/.local`, sau đó cả 2 agent healthy.
- Tải nền cho bài 7.4–7.6: Locust headless **30 user, ramp 5** (`--headless -u 30 -r 5`) chạy liên tục.

### 4.1 Screenshot

- [x] Dashboard Grafana khi đang có tải: `screenshots/grafana-dashboard.png` (18.7 req/s, error 0.97%, p95 291.5 ms, 2 instance)
- [x] Một trace chậm trong Jaeger: `screenshots/jaeger-slow-trace.png`
- [x] Một trace lỗi trong Jaeger có event `exception`: `screenshots/jaeger-error-trace.png`
- [x] Trang Alerts của Prometheus với **2 alert cùng firing** (`LLMCostBurnRateHigh` + `AgentHighErrorRate`): `screenshots/prometheus-alert-firing.png`
- Ảnh thêm: `grafana-expB.png`, `grafana-expD.png`, `grafana-expE.png`, `grafana-scaling.png`

**Trả lời Exercise 7.4.** Số đọc từ Prometheus lúc 15:34:34, 30 user, 2 instance:

1. **p95 = 292 ms, avg = 230 ms, p50 = 173 ms, p99 = 1823 ms.** p95 chỉ cao hơn avg khoảng 60 ms, nghĩa là 95% request khá đồng đều. Nhưng p99 lên tới 1.8 s cho thấy khoảng 3% request rơi vào tail (+1.5 s từ `MOCK_SLOW_RATE=0.03`). Avg bị kéo lên bởi chính nhóm này (avg > p50), nên avg không đại diện cho user điển hình lẫn user chậm.
2. **Requests/s theo instance:** 9.33 so với 9.35 req/s, tức Nginx chia **đều** round-robin cho 2 instance (cũng thấy `X-Served-By` luân phiên `172.22.0.7` / `172.22.0.8`).
3. **Input tokens/s (1435) lớn hơn output tokens/s (750)** vì prompt gửi LLM = system prompt + tài liệu retrieval (RAG context) + câu hỏi. Câu trả lời của mock chỉ là một đoạn tài liệu, ngắn hơn toàn bộ prompt.
4. **`curl http://localhost:8080/metrics` trả `404 Not Found` từ Nginx**, vì `nginx.conf` có `location = /metrics { return 404; }`. `/metrics` chỉ dành cho Prometheus scrape trực tiếp từng agent trong mạng nội bộ Docker (`dns_sd_configs`). Không public ra ngoài vì metrics lộ thông tin nội bộ (route, model, cost, version), và nếu đi qua load balancer thì mỗi lần scrape rơi vào một instance khác nhau, làm số liệu nhảy lung tung.

**Phát hiện thêm:** ngay ở tải nền 30 user (khoảng 18.7 req/s), `LLMCostBurnRateHigh` **đã firing** (khoảng $2.41/giờ, vượt ngưỡng $2/giờ; pending lúc 15:23:57). Ngưỡng $2/giờ trong `alert_rules.yml` thấp hơn mức chi phí bình thường của chính tải này, nên production thật phải đặt ngưỡng theo budget và traffic dự kiến.

### 4.2 Thí nghiệm "phá hệ thống"

Cách làm: ghi biến vào `production/.env` → `docker compose -p k5-day14-observability up -d agent` (tạo lại agent) → cứ 5 giây đọc `/api/v1/alerts` một lần (script `exp.py`). Thời gian tính từ lúc chạy lệnh đổi cấu hình. Locust 30 user chạy liên tục.

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `AgentHighErrorRate` (pending +26.5 s) | **86.7 s** | Error rate (stat chuyển đỏ, 22.8%), "Requests/s theo status code" (503 tăng vọt), "LLM calls theo kết quả" (error) |
| B | `MOCK_SLOW_RATE=0.3` | `AgentHighLatencyP95` (pending +27.4 s) | **147.8 s** | Latency p50/p95/p99: p95 1.91 s, p99 1.98 s, avg chỉ 0.62 s; LLM latency p95 1.91 s |
| C | `LLM_MODEL=mock-llm-large` | `LLMCostBurnRateHigh` (pending +22.7 s) | **143.2 s** | Cost burn rate tăng từ $1.81 lên $16.33/giờ (vẫn đang tăng vì cửa sổ `[5m]`); LLM latency p95 tăng từ 198 lên 496 ms; bảng version đổi sang `mock-llm-large` |
| D | `docker pause k5-day14-observability-agent-1` | `AgentInstanceDown` cho `172.22.0.7:8000` (pending +5.2 s) | **35.3 s** | "Instances up" giảm còn 1; "Requests/s theo instance" mất 1 đường; Error rate của agent vẫn xanh (1.8%) |
| E | `AGENT_VERSION=v1` | **Không có** (quan sát 180 s) | — | Tokens/s giảm khoảng 53% (input 1448 → 676, output 761 → 369); bảng "Phiên bản đang chạy" đổi sang `v1`; p95 còn giảm (292 → 199 ms), cost giảm còn $1.63/giờ |

Ghi chú khi phân tích:
- **B:** Locust là tải vòng kín (user chờ response rồi mới gửi tiếp), nên khi chậm đi thì req/s tụt từ khoảng 19 xuống 15.2. Chi phí giảm theo xuống $1.81/giờ và alert cost tự **resolved**: latency xấu đi lại làm một alert khác "khỏi".
- **D:** lần chạy này *không* thấy 504. Nginx (`proxy_next_upstream error timeout`, `resolve`) đã chuyển hết traffic sang instance còn lại, 6 request gọi thử đều 200 trong khoảng 0.16–0.21 s. Nhưng log Nginx cho thấy một lỗi khác mà dashboard agent **không hề thấy**: mỗi lần `docker compose up -d agent` tạo lại *cả 2 agent cùng lúc*, Nginx trả **khoảng 220–240 lỗi 502** ("no live upstreams") trong vài giây, ví dụ 217 lỗi lúc 08:35 UTC và 233 lỗi lúc 08:43 UTC (`docker compose logs nginx`). Request bị 502/504 ở Nginx chưa bao giờ tới agent, nên metric của agent không đếm. Muốn thấy phải giám sát ở tầng load balancer (log/metric Nginx) hoặc synthetic check từ ngoài vào, và deploy theo kiểu rolling thay vì tạo lại mọi instance cùng lúc.
- **D:** dùng `pause` chứ không dùng `stop` vì container bị stop biến mất khỏi Docker DNS. Khi đó Prometheus (`dns_sd_configs`) coi target không còn tồn tại (không phải "down") nên không có alert. Container bị pause vẫn còn trong DNS nhưng không trả lời, nên `up == 0`.
- **Mọi thí nghiệm:** ngay sau khi tạo lại agent, `AgentInstanceDown` luôn chuyển sang pending khoảng 5–8 giây (IP cũ biến mất) rồi tự hết, không bao giờ firing nhờ `for: 30s`. Đây đúng là tác dụng chống báo động giả của `for`.

**Thí nghiệm E không làm alert nào firing. Phát hiện loại sự cố này trong production bằng cách nào?**

Đổi sang v1 làm chất lượng sụp từ 100% xuống khoảng 4% (theo eval ở mục 3), nhưng mọi metric hạ tầng vẫn xanh. p95 và cost còn *đẹp hơn*, và trong lần chạy này panel "Retrieval hit ratio" vẫn đứng 100%, không phản ánh gì. Sự cố về **chất lượng** không làm request lỗi hay chậm, nên metric "golden signals" không bắt được. Cách phát hiện:
1. **Eval làm cổng trước khi deploy:** chạy golden dataset trong CI và CD (job `quality-gate`), so `--baseline` với bản đang chạy. Thay đổi v2 sang v1 sẽ bị chặn từ PR (accuracy 4.3%, exit code 1).
2. **Eval liên tục trên production:** canary hoặc shadow traffic, định kỳ chạy golden dataset vào endpoint thật và đẩy `eval_accuracy` thành metric Prometheus, rồi đặt alert khi nó giảm dưới SLO.
3. **Theo dõi tín hiệu gián tiếp:** token/request giảm đột ngột (ở đây giảm khoảng 53%), `agent_info{version}` thay đổi ngoài lịch deploy, tỉ lệ retrieval rỗng; thêm feedback người dùng (thumbs down, tỉ lệ hỏi lại) và LLM-as-judge chấm mẫu ngẫu nhiên.

### 4.3 Debug bằng trace

Trace chậm nhất trong 6 phút cuối, ở các container hiện tại, tìm qua Jaeger API v3. Ảnh: `screenshots/jaeger-slow-trace.png`.

- `trace_id`: `a237e47d0e10cdffe4e0e2bbaea528fe`
- Tổng thời gian: **1733.4 ms** (`POST /ask`, HTTP 200, instance `99f803e00877`)
- Span chiếm nhiều thời gian nhất: **`chat mock-llm-small` 1709.1 ms (98.6%)**. Các span khác: `agent.run` 1730.6 ms (span cha), `agent.retrieve_context` 21.3 ms (1.2%); `gen_ai.usage.input_tokens=76`, `output_tokens=39`. Kết luận: chậm do upstream LLM (request rơi vào tail +1.5 s), không phải do code agent hay retrieval.
- Dòng log tương ứng (grep theo `trace_id`):

```json
{"ts": "2026-11-26T08:52:12.396976+00:00", "level": "INFO", "service": "observable-agent", "instance": "99f803e00877", "event": "llm_call", "trace_id": "a237e47d0e10cdffe4e0e2bbaea528fe", "span_id": "e35334627f48e265", "request_id": "79cacac215ea", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 76, "output_tokens": 39, "cost_usd": 3.5e-05, "llm_ms": 1692.6}
{"ts": "2026-11-26T08:52:12.397998+00:00", "level": "INFO", "service": "observable-agent", "instance": "99f803e00877", "event": "http_request", "trace_id": "a237e47d0e10cdffe4e0e2bbaea528fe", "span_id": "e35334627f48e265", "request_id": "79cacac215ea", "method": "POST", "route": "/ask", "status": 200, "ms": 1732.6}
```

Trace lỗi (`screenshots/jaeger-error-trace.png`): `trace_id` `51163c40ef53673ae70740448e7617ab`, tổng 95.0 ms, HTTP 503. Span `chat mock-llm-small` (71.7 ms) có `error.type=upstream_unavailable`, status ERROR và event `exception` (`utils.mock_llm.LLMError: Upstream LLM error: 503 Service Unavailable (mock)`, kèm stacktrace). Log tương ứng:

```json
{"ts": "2026-11-26T08:53:58.063654+00:00", "level": "INFO", "service": "observable-agent", "instance": "99f803e00877", "event": "llm_error", "trace_id": "51163c40ef53673ae70740448e7617ab", "span_id": "696a6573a9764547", "request_id": "74a8c2e5af1f", "error": "Upstream LLM error: 503 Service Unavailable (mock)"}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, ramp 10/s, 2 phút)

Lệnh: `docker compose -p k5-day14-observability up -d --scale agent=N`, chờ 25 giây, rồi `docker compose -p k5-day14-observability run --rm locust -f /mnt/locust/locustfile.py --host http://nginx --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_N`. Cấu hình mặc định (v2, small, slow 0.03, error 0.01). Số liệu dòng `POST /ask` trong `benchmark-results/scale_1_stats.csv` và `scale_3_stats.csv`:

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 | 60.34 | 190 | 230 | 1700 | 79 / 7214 (1.10%), toàn bộ là 503 từ mock LLM |
| 3 | 60.41 | 190 | 220 | 1700 | 71 / 7218 (0.98%), toàn bộ là 503 từ mock LLM |

(Dòng Aggregated, gồm cả `/health`: 1 instance 65.77 req/s, avg 222 ms; 3 instance 66.57 req/s, avg 209 ms.)

**Giải thích kết quả:** scale từ 1 lên 3 instance gần như **không đổi gì**: RPS 60.3 so với 60.4, p95 230 so với 220 ms, p99 vẫn 1700 ms. Lý do:
1. **Tải do số user quyết định, không phải do server.** Locust là vòng kín: mỗi user gửi một request, chờ khoảng 0.2 s, rồi nghỉ 0.5–2 s (trung bình 1.25 s). 100 user × 1 / (1.25 + 0.22) ≈ 68 req/s, khớp với khoảng 66 req/s đo được. 1 instance đã phục vụ hết lượng tải này mà chưa bão hoà (p50 vẫn 190 ms như lúc không tải), nên thêm instance không có gì để "gánh hộ".
2. **Nút thắt latency nằm ở LLM upstream.** Theo trace, khoảng 98% thời gian của một request nằm trong span `chat mock-llm-*` (khoảng 190 ms, và +1.5 s cho 3% request chậm). Thêm instance không làm từng lời gọi LLM nhanh hơn, nên p50/p95/p99 giữ nguyên; p99 khoảng 1.7 s là tail của chính LLM.
3. Scale chỉ có lợi khi một instance bắt đầu bão hoà (như mục 2.1, khi concurrency 100 trên 1 process làm p95 vọt lên 1.76 s). Muốn thấy khác biệt cần tăng user tới mức 1 instance (0.5 CPU) bắt đầu xếp hàng. Còn muốn giảm tail latency thì phải xử lý ở tầng LLM: timeout + retry/hedging, cache câu trả lời, model nhanh hơn, streaming.

---

## 6. Kết luận

- 3 điều học được về benchmark & observability:
  1. **Average nói dối.** 3% request chậm chỉ làm avg tăng khoảng 50 ms nhưng p99 tăng gấp 8 lần; 10% chậm thì p95 vỡ SLO trong khi avg trông vẫn "chấp nhận được". SLO và alert phải đặt theo percentile, tính từ histogram (`histogram_quantile`).
  2. **Metric chỉ thấy những gì chính service đó thấy.** Lỗi 502 ở Nginx khi tạo lại agent và việc tụt chất lượng từ v2 xuống v1 đều không làm dashboard agent đỏ. Cần kết hợp metric + trace + log có chung `trace_id`, giám sát ở tầng load balancer, và eval chất lượng như một "SLI" riêng.
  3. **So sánh phải theo đơn vị giá trị.** v2 tốn gần gấp đôi token mỗi câu nhưng rẻ hơn khoảng 11 lần trên mỗi câu đúng; scale 3 instance không giúp gì khi nút thắt là LLM upstream. Phải đo trước rồi mới tối ưu, và đo đúng chỉ số.
- Nếu đưa agent này lên production thật, sẽ thêm:
  - Metric `eval_accuracy` (eval định kỳ hoặc canary) kèm alert khi giảm dưới 85%, và alert khi token/request thay đổi đột ngột (dấu hiệu prompt hoặc retrieval hỏng).
  - Metric và alert ở tầng Nginx/LB (tỉ lệ 5xx của upstream, `no live upstreams`) cùng synthetic check từ bên ngoài (`/health`, một câu `/ask` mẫu).
  - Alert error budget burn rate nhiều cửa sổ (5m/1h) thay cho ngưỡng cố định; alert cost theo % budget ngày/tháng thay vì $2/giờ cố định (ngưỡng hiện tại firing ngay ở tải bình thường).
  - Alert p99 LLM latency theo từng model, và theo dõi số retry/timeout tới upstream.
