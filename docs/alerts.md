# Alert và Runbook

Mỗi alert dựa trên triệu chứng người dùng hoặc SLO, không dựa trực tiếp vào tên implementation nội bộ. Rule nằm tại [`../config/alert_rules.yaml`](../config/alert_rules.yaml), SLO tại [`../config/slo.yaml`](../config/slo.yaml). Luồng điều tra chung: **Metrics → Logs → Traces**. Công cụ dùng trong runbook:

- Dashboard: `python scripts/dashboard.py --serve` → http://127.0.0.1:8050
- Metrics tức thời: `curl http://127.0.0.1:8000/metrics`
- Logs: `data/logs.jsonl` (lọc theo `event`, `latency_ms`, `correlation_id`)
- Traces: Langfuse project `day13-k4-l3a-<MSSV>`, lọc theo metadata `correlation_id`

## Alert 1: ChatLatencyP95High

- Tên: `ChatLatencyP95High`
- Severity: P2-warning
- Duration: 5m
- Kênh thông báo: Slack `#day13-l3a-oncall`
- SLI/SLO liên quan: `fast_successful_requests` (99,5% request có `latency_ms <= 3000`, cửa sổ 28 ngày)
- Điều kiện và thời gian duy trì: `p95(latency_ms)` của `response_sent` > 2000 ms liên tục 5 phút
- Ảnh hưởng tới người dùng: câu trả lời chậm rõ rệt (baseline P95 ~0,2–0,5 s); nếu kéo dài sẽ vượt ngưỡng SLO 3000 ms và tiêu error budget
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel *Latency percentiles and TTFT*: xác định thời điểm P95 tăng và TTFT P95 có tăng theo không. TTFT bình thường mà latency tăng → chậm nằm **trước** LLM (retrieval/prompt fetch); TTFT tăng → chậm ở LLM.
  2. Lọc log chậm và lấy correlation ID:
     `python -c "import json;[print(r['ts'],r['correlation_id'],r['latency_ms']) for r in map(json.loads,open('data/logs.jsonl',encoding='utf-8')) if r.get('event')=='response_sent' and r['latency_ms']>2000]"`
  3. Mở trace có metadata `correlation_id` đó trên Langfuse, so sánh latency của `rag-retrieve`, `llm-generate` và root `lab-agent-run`.
- Mitigation tạm thời: nếu `rag-retrieve` chậm → chuyển sang index/replica dự phòng hoặc giảm top-k, đặt timeout cho retrieval; nếu `llm-generate` chậm → chuyển model nhỏ hơn hoặc giảm `max_tokens`; nếu chậm do prompt fetch → kiểm tra Langfuse status, app đã có fallback template local. Practice: `python scripts/inject_incident.py --scenario rag_slow --disable`.
- Owner: nguyen-vu-huy (on-call LLM API)

## Alert 2: ChatErrorRateHigh

- Tên: `ChatErrorRateHigh`
- Severity: P1-critical
- Duration: 5m
- Kênh thông báo: Slack `#day13-l3a-oncall` (kèm page on-call)
- SLI/SLO liên quan: `fast_successful_requests`; guardrail `error_rate_pct_max: 2`, `retrieval_success_rate_pct_min: 90`
- Điều kiện và thời gian duy trì: `count(request_failed) / count(request_received) > 2%` **hoặc** retrieval success < 90% liên tục 5 phút
- Ảnh hưởng tới người dùng: người dùng nhận HTTP 500 và không có câu trả lời; mỗi request lỗi trực tiếp tiêu error budget
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel *Error rate and retrieval success*: đọc `error_type` breakdown và xem retrieval success có giảm cùng lúc không.
  2. Lọc log `event == "request_failed"`, đọc `error_type`, `tool_name`, `payload.detail` và lấy `correlation_id`.
  3. Mở trace tương ứng: observation nào có `level=ERROR`/`statusMessage` (ví dụ `rag-retrieve` với `RuntimeError`).
- Mitigation tạm thời: nếu lỗi ở retrieval (vector store timeout) → bật fallback trả lời không cần docs hoặc retry có backoff, chuyển sang replica; nếu lỗi sau deploy/đổi prompt → rollback deploy hoặc `python scripts/prompt_versions.py rollback <version>`. Practice: `python scripts/inject_incident.py --scenario tool_fail --disable`.
- Owner: nguyen-vu-huy (on-call LLM API)

## Alert 3: ChatCostPerRequestSpike

- Tên: `ChatCostPerRequestSpike`
- Severity: P3-ticket
- Duration: 10m
- Kênh thông báo: Slack `#day13-l3a-cost`
- SLI/SLO liên quan: guardrail `daily_cost_usd_max: 2.5`; theo dõi thêm `quality_score_avg_min: 0.75`
- Điều kiện và thời gian duy trì: `avg(cost_usd)` của `response_sent` > 0,004 USD (≈2 lần baseline 0,0021 USD) liên tục 10 phút
- Ảnh hưởng tới người dùng: chưa gây lỗi ngay, nhưng câu trả lời dài bất thường (chậm hơn, khó đọc) và ngân sách ngày có thể cạn, dẫn tới phải chặn traffic
- Ba bước kiểm tra đầu tiên:
  1. Dashboard panel *Input and output tokens* và *Cost over time*: chi phí tăng do `tokens_in` (prompt/context dài) hay `tokens_out` (output dài)?
  2. Lọc log `response_sent` có `cost_usd` cao, lấy `correlation_id` và so sánh `feature`, `tokens_out`.
  3. Mở trace, xem generation `llm-generate`: `usage_details`, `cost_details` và prompt name/version. Có trùng với lần đổi label `production` gần nhất không?
- Mitigation tạm thời: rollback prompt về version trước (`python scripts/prompt_versions.py rollback <version>`), giới hạn `max_tokens`, chuyển feature tốn kém sang model rẻ hơn. Practice: `python scripts/inject_incident.py --scenario cost_spike --disable`.
- Owner: nguyen-vu-huy (LLMOps/FinOps)
