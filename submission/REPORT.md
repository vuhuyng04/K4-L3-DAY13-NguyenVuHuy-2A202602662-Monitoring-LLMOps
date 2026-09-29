# Báo cáo cá nhân — K4-L3A Day 13 Monitoring & LLMOps

> Evidence dẫn bằng đường dẫn tương đối từ thư mục `submission/`.

## 1. Thông tin học viên

- **Họ và tên:** Nguyễn Vũ Huy
- **MSSV:** 2A202602662
- **Lớp:** K4-L3A
- **Repository URL:** https://github.com/vuhuyng04/K4-L3-DAY13-NguyenVuHuy-2A202602662-Monitoring-LLMOps
- **Commit SHA cuối:** `<<điền SHA sau commit cuối>>`
- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1`
- **Tên project Langfuse cá nhân:** `day13-k4-l3a-2A202602662`

## 2. Evidence index

| Evidence | Đường dẫn |
|---|---|
| Pytest cuối | [evidence/01-pytest.txt](evidence/01-pytest.txt) |
| Log validator | [evidence/02-log-validator.txt](evidence/02-log-validator.txt) |
| Dashboard validator | [evidence/03-dashboard-validator.txt](evidence/03-dashboard-validator.txt) |
| Structured log | [evidence/04-structured-log.txt](evidence/04-structured-log.txt) |
| PII redaction | [evidence/05-pii-redaction.txt](evidence/05-pii-redaction.txt) |
| Trace list | [evidence/06-trace-list.png](evidence/06-trace-list.png) |
| Trace waterfall | [evidence/07-trace-waterfall.png](evidence/07-trace-waterfall.png) (trace `req-5c2b5962`, practice `rag_slow`) |
| Trace metadata | [evidence/08-trace-metadata.png](evidence/08-trace-metadata.png) |
| Prompt versions | [evidence/09-prompt-versions.png](evidence/09-prompt-versions.png) |
| Prompt rollback | trước: [evidence/10a-prompt-promote-v2.png](evidence/10a-prompt-promote-v2.png) (production = v2) → sau: [evidence/10-prompt-rollback.png](evidence/10-prompt-rollback.png) (production = v1); log CLI: [evidence/10-prompt-rollback-cli.txt](evidence/10-prompt-rollback-cli.txt) |
| Dashboard runtime | [evidence/11-dashboard-overview.png](evidence/11-dashboard-overview.png) |
| Incident metric | [evidence/12-incident-metric.png](evidence/12-incident-metric.png) |
| Incident log | [evidence/13-incident-log.txt](evidence/13-incident-log.txt) |
| Incident trace | [evidence/14-incident-trace.png](evidence/14-incident-trace.png), [evidence/14b-incident-trace-span.png](evidence/14b-incident-trace-span.png) |
| Baseline (trước khi sửa) | [evidence/baseline/](evidence/baseline/) |

## 3. Kết quả kỹ thuật

| Nội dung | Baseline | Kết quả cuối | Nhận xét |
|---|---|---|---|
| `validate_logs.py` | 30/100 (thiếu required fields, 0 correlation ID, thiếu enrichment) | 100/100 | 306 log record, 151 correlation ID duy nhất, 0 record thiếu field |
| `validate_dashboard.py` | 6/6 (contract có sẵn) | 6/6 | Có thêm dashboard runtime `scripts/dashboard.py` |
| `pytest` | 22 passed | 34 passed | Thêm test PII, middleware, failure path, dashboard |
| Số traces hợp lệ | 10 root span, không có child, `correlation_id=MISSING` | 106 trace có `correlation_id`, mỗi trace có root + 2 child | Kiểm bằng Langfuse `GET /api/public/v2/observations` |
| Số PII leak | 0 (starter chỉ log preview đã scrub) | 0; log/trace không còn email, SĐT, CCCD, thẻ, hộ chiếu | Scrub mọi field trước khi ghi, không chỉ `payload` |
| Latency P95 / TTFT P95 | ~415 ms (client) / chưa đo | 157 ms / 50 ms (trạng thái ổn định) | Trong `rag_slow`: P95 2655 ms |
| Retrieval success rate | không đo | 100% khi bình thường; 91,7% trên toàn bộ log (gồm 10 request `tool_fail`) | Panel errors hiển thị riêng |

## 4. Logging và PII

- **Cách tạo/nhận và truyền correlation ID:** [app/middleware.py](../app/middleware.py) gọi `clear_contextvars()` ở đầu mỗi request (và lần nữa sau khi xong), nhận header `x-request-id` nếu đúng format `req-<8-hex>`, ngược lại sinh `req-<uuid4[:8]>`. Chỉ chấp nhận đúng format để chặn log injection (xuống dòng, JSON giả) và ID quá dài. ID được bind vào structlog contextvars, lưu ở `request.state` để truyền vào agent/trace, và trả lại qua header `x-request-id` và `x-response-time-ms`. Khi request lỗi 500, body cũng có `correlation_id`.
- **Các metadata được ghi vào structured log:** `ts`, `level`, `service`, `event`, `correlation_id`, `user_id_hash` (SHA-256 cắt 12 ký tự, không log user_id thô), `session_id`, `feature`, `model`, `env`; với `response_sent` thêm `latency_ms`, `ttft_ms`, `tokens_in/out`, `cost_usd`, `quality_score`, `tool_name`, `tool_success`; với `request_failed` thêm `error_type`, `tool_success=false`.
- **Cách bảo đảm PII được scrub trước khi ghi:** processor `scrub_event` trong [app/logging_config.py](../app/logging_config.py) chạy **sau** `format_exc_info` (để stack trace cũng được scrub) và **trước** `JsonlFileProcessor`/`JSONRenderer`. Processor scrub đệ quy mọi giá trị chuỗi trong event (kể cả dict/list lồng nhau), không chỉ `payload`. Pattern trong [app/pii.py](../app/pii.py): email, thẻ 16 số, CCCD 12 số, SĐT Việt Nam (`0`/`+84`, có dấu cách/chấm/gạch), hộ chiếu. Thứ tự pattern có chủ đích: thẻ chạy trước CCCD/SĐT để không bị che một phần rồi lộ phần còn lại. Trên trace, chỉ gửi `summarize_text()` (đã scrub) cho query/prompt/output; root observation dùng `capture_input=False`.
- **Cách kiểm chứng kết quả:** `validate_logs.py` đạt 100/100; [05-pii-redaction.txt](evidence/05-pii-redaction.txt) gửi input có đủ 5 loại PII giả và grep lại `data/logs.jsonl` được 0 lần xuất hiện; trace của cùng request (`req-0000a11e`) chỉ chứa `[REDACTED_*]`. Test: [tests/test_pii.py](../tests/test_pii.py), [tests/test_middleware.py](../tests/test_middleware.py) (gồm test hai request liên tiếp không rò `user_id_hash` của nhau).

## 5. Tracing và prompt versioning

- **Cách xác nhận traces do chính tôi tạo trong project cá nhân:** key trong `.env` là của project `day13-k4-l3a-2A202602662`; mọi trace có metadata `correlation_id` trùng với log do máy tôi sinh ra trong `data/logs.jsonl`. Đã kiểm bằng API `GET /api/public/v2/observations` (endpoint `/api/public/traces` cũ trả 410 với org tạo sau 16/09/2026).
- **Cấu trúc root/retrieval/generation observations:** [app/agent.py](../app/agent.py)
  - `lab-agent-run` (type `agent`, root, `@observe`): metadata `correlation_id`, `feature`, `model`, `doc_count`, `query_preview`, `prompt_name/label/version/source`.
    - `rag-retrieve` (type `retriever`): input `query_preview`, output `doc_count` + `docs_preview`, metadata `retrieval_ms`; khi lỗi đặt `level=ERROR` và `status_message`.
    - `llm-generate` (type `generation`): `model`, link tới managed prompt, `usage_details` (input/output), `cost_details` (input/output/total), `completion_start_time` = start + TTFT (nên Langfuse tính được time-to-first-token).
  - Child observation được tạo qua helper `start_observation()` trong [app/tracing.py](../app/tracing.py), dùng `start_as_current_observation` của SDK v4 nên tự lồng dưới root theo OTel context, kể cả khi agent chạy trong threadpool.
- **Cách nối trace với log:** cùng một `correlation_id` được truyền từ middleware → `agent.run(correlation_id=...)` → `propagate_attributes(metadata={"correlation_id": ...})`, nên có mặt trên mọi observation của trace. Trên Langfuse lọc Metadata `correlation_id = req-xxxx`; trong log thì `grep req-xxxx data/logs.jsonl`.
- **Ví dụ trace IDs (trích từ Langfuse):**

  | correlation_id | trace ID | ghi chú |
  |---|---|---|
  | req-94ed2434 | a66936400ff5097dc6878627c5827e36 | bình thường, 155 ms |
  | req-d983fe76 | 73c8c4f62e37db49e6c20ae11a8de660 | bình thường |
  | req-be08a526 | 98344d5394a3f6c13d43081a93698ff7 | bình thường |
  | req-d110ef5f | f2fc9179f94dee800963a76a3ab9974f | bình thường |
  | req-324f7e7a | 60bc82fa171186ca869b18a3a34009a8 | bình thường |
  | req-d947b0e1 | 03b40832d82ef9208575042ad50a2653 | bình thường |
  | req-0000106a | 33ea712cddf184f940a4604288a12eac | request trong evidence 04 |
  | req-0000a11e | 6aa208e58754cabb1bc07a59b6e67365 | request PII demo (evidence 05) |
  | req-5c2b5962 | a3fddacfb14b4b1921899bff8dd1df5e | practice `rag_slow`: `rag-retrieve` 2,50 s / root 2,655 s |
  | req-54ab5718 | 5f1764fdfe9a5dcd39c4c0419350d852 | practice `tool_fail`: level ERROR |

- **Prompt name:** `day13-chat` (text prompt, biến `{{feature}}`, `{{docs}}`, `{{message}}`), tạo bằng [scripts/prompt_versions.py](../scripts/prompt_versions.py).
- **Version/label baseline:** v1, labels `baseline` + `production`. Template gốc `Feature/Docs/Question`.
- **Version/label candidate:** v2, label `candidate`. Thêm dòng "Answer in at most 3 short bullet points and only use the Docs above." Cùng input, `tokens_in` tăng từ 32 lên 50.
- **Trace ID của mỗi version** (cùng input "Explain why metrics traces and logs work together"):

  | Bước | correlation_id | trace ID | prompt link trên generation |
  |---|---|---|---|
  | label `baseline` | req-b1000001 | 084c1429a79c6e9b4092c7f3742f0f6f | day13-chat v1 |
  | label `candidate` | req-c1000002 | bf4e01c037a6e4e6fb3cfcad75df645f | day13-chat v2 |
  | `production` sau promote | req-d1000003 | 5b7f8795f9b993a94471fb54c6b49619 | day13-chat v2 |
  | `production` sau rollback | req-e1000004 | 1fc9afcfc9b1c9a0c4bb78b0b83cc383 | day13-chat v1 |

- **Cách promote và rollback `production`:** `python scripts/prompt_versions.py promote 2` rồi `rollback 1` (gọi `update_prompt(new_labels=["production"])`; Langfuse chỉ cho một version giữ một label nên label tự rời version cũ). App đọc label qua `LANGFUSE_PROMPT_LABEL=production` nên **không cần deploy lại code**; vì SDK cache prompt 60 giây, thay đổi có hiệu lực sau tối đa 60 giây hoặc sau khi restart. Log đầy đủ ở [10-prompt-rollback-cli.txt](evidence/10-prompt-rollback-cli.txt). Nếu Langfuse không trả được prompt, app dùng template local và ghi `prompt_source=local-fallback` thay vì giả version.

## 6. Dashboard, SLO và alerts

- **Dashboard và sáu panel:** [scripts/dashboard.py](../scripts/dashboard.py) đọc `data/logs.jsonl` và contract [config/dashboard.yaml](../config/dashboard.yaml) (panel, đơn vị, threshold lấy từ YAML, không hard-code). Chạy `python scripts/dashboard.py --serve` (http://127.0.0.1:8050, meta refresh 30 giây, cửa sổ 60 phút) hoặc `--out file.html --until <ISO>` để xuất snapshot của một khoảng sự cố. Sáu panel:
  1. Latency: P50/P95/P99 và TTFT P95 theo phút, ngưỡng P95 ≤ 3000 ms.
  2. Traffic: số request/phút, ngưỡng ≥ 1 rpm.
  3. Errors: error rate % và retrieval success %, breakdown theo `error_type`, ngưỡng ≤ 2%.
  4. Cost: chi phí tích lũy trong cửa sổ và cao nhất/phút, ngưỡng tổng ≤ 2,5 USD.
  5. Tokens: `tokens_in`/`tokens_out` tích lũy, ngưỡng mỗi field ≤ 50.000.
  6. Quality: mean `quality_score`, ngưỡng ≥ 0,75.

  Mỗi panel có đơn vị, badge "Trong ngưỡng/Vượt ngưỡng" (icon + chữ, không chỉ dựa vào màu), đường threshold nét đứt, và tooltip khi hover. Ảnh: [11-dashboard-overview.png](evidence/11-dashboard-overview.png). Test tính toán: [tests/test_dashboard_runtime.py](../tests/test_dashboard_runtime.py).
- **SLO và lý do chọn:** [config/slo.yaml](../config/slo.yaml): 99,5% request có `response_sent` với `latency_ms ≤ 3000` trên tổng `request_received`, cửa sổ 28 ngày. Baseline đo được: P50 ~154 ms, P95 ~157 ms (cache ấm), TTFT P95 50 ms. Tôi giữ 3000 ms để SLO và dashboard dùng chung một con số. Mức 99,5% (không phải 99,9%) vì app phụ thuộc vector store và LLM bên ngoài.
- **Cách tính error budget:** budget = (1 − 0,995) × tổng request trong 28 ngày = 0,5%. Ví dụ 10.000 request/ngày → 280.000 request/28 ngày → được phép 1.400 request lỗi hoặc chậm. Burn rate 14,4x trong 1 giờ (tiêu ~2% budget mỗi giờ) thì page ngay; 6x trong 6 giờ thì tạo ticket.
- **Ba alert và runbook tương ứng** ([config/alert_rules.yaml](../config/alert_rules.yaml), [docs/alerts.md](../docs/alerts.md)):
  1. `ChatLatencyP95High`: P2, P95 > 2000 ms trong 5 phút. Đây là cảnh báo sớm, vì practice `rag_slow` chỉ đẩy latency lên ~2655 ms, **dưới** ngưỡng SLO 3000 ms, nên nếu alert đặt ở 3000 ms sẽ không bao giờ kêu.
  2. `ChatErrorRateHigh`: P1, error rate > 2% **hoặc** retrieval success < 90% trong 5 phút.
  3. `ChatCostPerRequestSpike`: P3, chi phí trung bình > 0,004 USD/request (≈ 2 lần baseline 0,0021 USD) trong 10 phút.

  Cả ba đều gửi Slack (`#day13-l3a-oncall` / `#day13-l3a-cost`), có owner và runbook ba bước theo luồng Metrics → Logs → Traces.

## 7. Điều tra challenge

File challenge do Lab Coach gửi được lưu tại `config/challenge.json` (không sửa, đã `.gitignore`, không commit). Đã chạy `python scripts/inject_incident.py` rồi `python scripts/load_test.py --challenge --concurrency 5`. Challenge gồm 5 query của feature `monitoring`, ngưỡng `latency_threshold_ms = 2000`.

- **Challenge ID:** `day13-k4-l3a-monitoring-llmops-v1` (cohort K4)
- **Khoảng thời gian điều tra:** 2026-09-29, **09:17:21 → 09:17:26 UTC** (16:17 giờ VN). Mốc so sánh: workload bình thường lúc 09:16 UTC. Xử lý lúc 09:36:40 UTC, xác nhận hồi phục ngay sau đó.
- **Triệu chứng từ metrics** ([12-incident-metric.png](evidence/12-incident-metric.png)):

  | Chỉ số | 09:16 (bình thường) | 09:17 (sự cố) |
  |---|---:|---:|
  | Latency P50 / P95 / P99 | 153 / 155 / 155 ms | 2654 / 2655 / 2655 ms |
  | TTFT P95 | 51 ms | 51 ms |
  | Error rate / retrieval success | 0% / 100% | 0% / 100% |

  P95 tăng ~17 lần và vượt ngưỡng 2000 ms của challenge. Trên dashboard, panel latency bật **"Điều kiện alert: ChatLatencyP95High"** (P95 > 2000 ms), còn ngưỡng SLO 3000 ms chưa bị vượt. Vì TTFT không đổi và không có lỗi, phần chậm nằm **trước** bước LLM chứ không phải ở model.
- **Log line và correlation ID liên quan** ([13-incident-log.txt](evidence/13-incident-log.txt)): lọc `response_sent` có `latency_ms > 2000` thì ra đúng 5 request challenge (`req-eeb02ed6`, `req-0799a78e`, `req-34503a7a`, `req-eb67e28b`, `req-b9361df2`). Chọn `req-eeb02ed6`:
  `{"event": "response_sent", "correlation_id": "req-eeb02ed6", "feature": "monitoring", "latency_ms": 2655, "ttft_ms": 51, "tool_name": "retrieval", "tool_success": true, "ts": "2026-09-29T09:17:26.083293Z", ...}`
- **Trace ID và span gây ảnh hưởng** ([14-incident-trace.png](evidence/14-incident-trace.png), [14b-incident-trace-span.png](evidence/14b-incident-trace-span.png)): trace `87361dc9440d9538f4d2a6a35e71958a` có metadata `correlation_id = req-eeb02ed6`, cùng request với log trên.

  | Span | Thời gian |
  |---|---:|
  | `lab-agent-run` (root) | 2,66 s |
  | `rag-retrieve` | **2,50 s** (`retrieval_ms = 2500`) |
  | `llm-generate` | 0,153 s |

  `rag-retrieve` chiếm ~94% thời gian. Cả 5 trace của challenge đều như vậy: `rag-retrieve` 2,501–2,503 s.
- **Root cause:** bước retrieval (vector store) trả kết quả chậm khoảng 2,5 s mỗi request (incident `rag_slow`). Kết quả retrieval vẫn đúng (`doc_count = 1`, `tool_success = true`), nên không phát sinh lỗi mà chỉ tăng latency. LLM, prompt (v1 `production`) và chi phí đều không đổi.
- **Fix action:** khôi phục retrieval về bình thường (`python scripts/inject_incident.py --disable`, tương đương failover sang replica/index khỏe). Chạy lại đúng 5 query challenge: `latency_ms` = 152–156 ms, P95 **156 ms**, xem cuối file [13-incident-log.txt](evidence/13-incident-log.txt).
- **Preventive measure:**
  1. Đặt timeout cho retrieval (ví dụ 800 ms, khoảng 5 lần P99 bình thường), quá hạn thì dùng câu trả lời fallback không cần docs và ghi `tool_success=false`. Latency khi đó bị chặn trên, còn sự cố chuyển thành tín hiệu lỗi retrieval mà `ChatErrorRateHigh` phát hiện được.
  2. Thêm SLI/alert riêng cho span retrieval (P95 của `retrieval_ms` > 500 ms trong 5 phút) để phát hiện trước khi latency tổng vượt SLO.
  3. Giữ alert `ChatLatencyP95High` ở 2000 ms. Sự cố này chỉ đẩy latency lên 2,66 s, dưới ngưỡng SLO 3000 ms, nên nếu chỉ alert theo SLO thì sẽ bỏ lỡ.
  4. Thêm health check/canary định kỳ cho vector store và replica để failover tự động.

*Diễn tập trước khi có challenge (practice `rag_slow`, không phải challenge chính thức):* dashboard báo latency P95 từ ~157 ms nhảy lên 2655 ms lúc 07:36 UTC, TTFT P95 vẫn 50 ms, nên chậm nằm **trước** LLM. Log `response_sent` của `req-5c2b5962` có `latency_ms=2655`, `ttft_ms=50`. Trace `a3fddacfb14b4b1921899bff8dd1df5e` cho thấy `rag-retrieve` = 2,502 s trên tổng 2,655 s, còn `llm-generate` = 0,152 s. Kết luận: retrieval chậm.

## 8. Giải thích và tự đánh giá

- **Một quyết định kỹ thuật quan trọng và lý do:** scrub PII ở **processor của logger** (một điểm chặn duy nhất, đệ quy mọi field, chạy trước writer) thay vì bắt từng chỗ gọi log phải tự scrub. Mọi log mới, kể cả exception text hay `payload.detail`, đều tự động được che; code gọi log quên scrub cũng không làm rò PII. Tương tự, trace chỉ nhận preview đã scrub. Đánh đổi: tốn một lần regex trên mọi field, không đáng kể so với latency của LLM.
- **Một lỗi/blocker đã gặp:**
  1. Khi bật `rag_slow` với `--concurrency 5`, server ghi `latency_ms ≈ 2,65 s` nhưng client phải chờ tới **13,3 s**.
  2. Bốn trace đầu tiên của thử nghiệm prompt không xuất hiện trên Langfuse.
- **Cách tìm nguyên nhân và xử lý:**
  1. So sánh `x-response-time-ms` (middleware) với `latency_ms` (agent) thấy chênh gần 5 lần, tức thời gian bị mất là thời gian **xếp hàng trước khi agent chạy**. Nguyên nhân: `async def chat` gọi `agent.run` đồng bộ (có `time.sleep`) nên chặn event loop, các request đồng thời chạy lần lượt. Sửa bằng `await run_in_threadpool(agent.run, ...)`; `run_in_threadpool` copy contextvars nên correlation ID và OTel context vẫn đi theo. Sau sửa, client chỉ còn ~2,67 s. Bài học: SLO đo ở agent không thấy được queueing, nên cần cả latency phía biên (`x-response-time-ms`).
  2. SDK gửi span theo batch, mà `terminate()` trên Windows kill process ngay nên batch chưa kịp gửi. Sửa: `flush()` trong lifespan shutdown và chờ flush trước khi tắt. Thêm một phát hiện nữa: request đầu tiên sau khi khởi động chậm ~2 s vì phải fetch prompt, nên tôi warm prompt cache trong lifespan (`prompt_cache_warmed`). Request chậm nhất sau restart giảm từ ~3 s xuống ~210 ms.
- **Cách hiểu luồng Metrics → Logs → Traces:** metrics (dashboard) trả lời *có vấn đề gì và từ khi nào*, rẻ và tổng hợp nên dùng để phát hiện và alert. Logs trả lời *request nào bị ảnh hưởng*: lọc theo khoảng thời gian và ngưỡng, lấy `correlation_id`. Trace của đúng `correlation_id` đó trả lời *bước nào gây ra*, nhờ so latency/level của từng span. Ba nguồn độc lập nhưng cùng khóa `correlation_id`, nên kết luận phải nhất quán giữa cả ba.
- **Vai trò của prompt version, token/cost, SLO hoặc rollback trong vận hành LLM:**
  - Prompt là "code" chạy trên production nhưng thay đổi không qua deploy. Gắn version và label vào từng generation giúp biết một regression (token tăng, quality giảm) bắt đầu từ version nào, và rollback chỉ bằng đổi label, không cần deploy.
  - Token/cost là chiều giám sát riêng của LLM: v2 tăng `tokens_in` từ 32 lên 50 (+56%) cho cùng input. Phép so sánh cùng input như vậy chính là cách đánh giá chi phí trước khi promote.
  - SLO/error budget quyết định khi nào dừng thay đổi (hết budget thì ưu tiên ổn định) và alert nào đáng để page người trực.
- **Điều quan trọng nhất đã học:** chỉ số đo ở một tầng có thể "đẹp" trong khi người dùng vẫn chịu trận (2,65 s so với 13,3 s). Cần đo ở biên, và phải kiểm chứng observability bằng sự cố giả lập chứ không chỉ bằng validator.
- **Hạn chế hoặc phần chưa hoàn thành, nếu có:**
  - Dashboard là trang HTML local, không có lưu trữ time-series lâu dài.
  - Alert mới ở dạng rule/runbook, chưa nối Alertmanager/Slack webhook thật.
  - Quality score vẫn là heuristic, chưa có LLM-as-judge.

### Bonus đã làm

- **Automation (CI + secret/PII scan):**
  - [scripts/scan_repo.py](../scripts/scan_repo.py) quét file được git track để tìm key Langfuse/OpenAI/AWS/private key, file cấm (`.env`, `config/challenge.json`, log) và PII. Allowlist chỉ gồm các fixture PII giả có chủ đích.
  - [.github/workflows/ci.yml](../.github/workflows/ci.yml) chạy scan, pytest, dashboard validator, rồi dựng API và chạy load test + `validate_logs` (yêu cầu 100/100) trên mỗi lần push.
  - [scripts/prompt_versions.py](../scripts/prompt_versions.py) tự động hóa việc setup/promote/rollback prompt.
- **Cải thiện hiệu năng có before/after trên cùng workload:**
  - Chuyển agent sang threadpool: `rag_slow` với concurrency 5, client latency 13,3 s → 2,67 s.
  - Warm prompt cache: request chậm nhất sau restart ~3 s → ~210 ms.

## 9. Checklist trước khi nộp

- [x] Kết quả và evidence thuộc commit SHA cuối.
- [x] Tất cả ảnh/output mở được bằng đường dẫn tương đối.
- [x] Incident evidence nối đúng metric → log → trace.
- [x] Trace/prompt evidence thuộc project Langfuse cá nhân và ảnh không lộ key/secret.
- [x] Repository chạy lại được theo README.
- [x] Không có secret, API key, PII thô hoặc evidence của người khác/lớp khác (`python scripts/scan_repo.py`: SẠCH).
- [ ] URL repo và commit SHA cuối đã được nộp trên LMS/Codelabs.
