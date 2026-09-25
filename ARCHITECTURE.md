# L3B Architecture Record

Team phải cập nhật tài liệu này cùng source. Mục tiêu là mô tả quyết định có thể kiểm chứng, không ghi prompt bí mật hoặc chain-of-thought.

## 1. System overview

Vẽ hoặc mô tả luồng từ input/candidate resolution đến MCP investigation, specialist agents, conflict resolver, verifier, output và trace.

```text
Input → Entity Resolver → Coordinator → Specialists → Conflict Resolver → Verifier → Output
            │                              │                  │             │
            └──────────────────────────── MCP ────────────────┴──────────── Trace
```

## 2. Agent ownership

| Actor             | Input                                            | Trách nhiệm                                                                                                    | Tool permission                               | Output/handoff                                              |
| ----------------- | ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ----------------------------------------------------------- |
| Entity/customer   | `customer_unique_id_hint`, `candidate_order_ids` | Gọi `get_customer_history`, chọn đúng order trong candidate, reject placeholder `candidate-*`                  | `get_customer_history`                        | `resolved_order_id` + `rejected_candidates` → Order/product |
| Coordinator       | Case input                                       | Phân công tuần tự các specialist, giữ correlation theo `case_id`                                               | Không gọi tool trực tiếp                      | `task_assigned` cho từng specialist                         |
| Order/product     | `resolved_order_id`                              | Gọi `get_order`, `get_order_items`, `get_sellers`, `get_product_context`; thu thập item/seller id              | 4 tool order domain                           | Entities → Shipment                                         |
| Shipment          | `resolved_order_id`                              | Gọi `get_shipment_summary`, xác định verdict (`on_time`/`seller_delay`/`logistics_delay`/…), `late_seller_ids` | `get_shipment_summary`                        | `shipment_analysis` → Payment/refund                        |
| Payment/refund    | `resolved_order_id`                              | Gọi `get_payment_timeline` (+ `get_refund_timeline` khi claim thuộc refund), tính captured/refunded/refundable | `get_payment_timeline`, `get_refund_timeline` | `payment_analysis` → Policy                                 |
| Policy            | `policy_version`, primary topic                  | Gọi `get_policy`, map rule → `case_status`, `recommended_action`, `refund_brl`, responsible parties            | `get_policy`                                  | `policy_decided` → Conflict resolver                        |
| Conflict resolver | Evidence của các specialist                      | Phát hiện multi-source conflict (order vs customer history, items vs shipment), chọn source ưu tiên            | Không gọi tool                                | `data_conflicts` → Verifier                                 |
| Verifier          | Output đã lắp ráp                                | Kiểm tra invariants trước finalize (xem §6)                                                                    | Không gọi tool                                | `verification_completed` → Output                           |

Áp dụng least privilege; tool discovery không đồng nghĩa mọi actor đều được gọi mọi tool.

## 3. Entity resolution và A2A protocol

- Xếp hạng candidate: ưu tiên `claimed_order_id` nếu là ID thật (không bắt đầu `candidate-`); fallback lấy order đầu tiên trong `get_customer_history`; cuối cùng là candidate thật đầu tiên. Các candidate `candidate-*` bị reject.
- Confidence: `0.95` khi có order thật, `0.50` khi không tìm thấy (`not_found`).
- Message envelope: mỗi tool call luôn kèm đúng `case_id`; `evidence_ref` trả về được gắn vào actor đã tiêu thụ nó.
- Correlation: toàn bộ sự kiện trace dùng `case_id` làm khóa; không dùng chéo evidence giữa case.
- Handoff: mỗi specialist kết thúc bằng `handoff` sang specialist tiếp theo; không có vòng lặp vì luồng là DAG tuyến tính (entity → order → shipment → payment → policy → conflict → verifier).
- Timeout: kế thừa timeout HTTP 300s của gateway; không retry lặp (xem §5).

## 4. Evidence và conflict lifecycle

- MCP response được `Contracts.validate_evidence` kiểm tra envelope (`schema_version`, `evidence_ref`, `result_hash`, `domain`, `data`) trước khi dùng.
- Mỗi lần dùng evidence, ghi `tool_result_consumed` kèm đúng `evidence_ref`; output chỉ chứa ref do gateway trả về, không tự tạo.
- Chọn source theo ưu tiên: `get_order` là nguồn authoritative cho order; `get_shipment_summary` cho timeline vận chuyển; `get_payment_timeline` cho payment lifecycle.
- Unresolved conflict được biểu diễn trong `data_conflicts` (field, sources, selected_source, resolution_code), tối đa 5 mục.
- Map evidence vào claim: claim chính → verdict `supported`/`unsupported`; claim `requested_full_refund` → `supported` (refund toàn phần), `partially_supported` (refund một phần) hoặc `unsupported` (không refund).
- Evidence không được tái sử dụng giữa các case: mọi ref được thu thập lại trong phạm vi từng case.

## 5. Failure and efficiency policy

| Failure                    |           Retry budget | Fallback                                                                      | Trace event/code                                                               |
| -------------------------- | ---------------------: | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| MCP timeout                |                      0 | Giữ nguyên giá trị cũ; không tạo dữ liệu phỏng đoán                           | `tool_result_consumed` bị bỏ qua, ghi `verification_completed` với checks giảm |
| Entity not found/ambiguous | 1 (thử candidate thật) | `status=not_found`, confidence 0.5                                            | `handoff` kèm `rejected_candidates`                                            |
| Source conflict            |                      0 | Chọn source ưu tiên, ghi `data_conflicts`                                     | `handoff` kèm `conflict_count`                                                 |
| Invalid specialist result  |                      0 | Dùng giá trị mặc định an toàn (`insufficient_evidence`/`needs_investigation`) | `policy_decided` + `verification_completed`                                    |

Query budget/cache: mỗi case gọi cố định 8 tool (`get_customer_history`, `get_order`, `get_order_items`, `get_sellers`, `get_product_context`, `get_shipment_summary`, `get_payment_timeline`, `get_policy`), chỉ thêm `get_refund_timeline` khi claim là `refund_pending`/`refund_failed`. Không gọi lặp, không cache xuyên case.

## 6. Verification invariants

- `case_id` khớp và output pass `l3b-output-v2.schema.json`.
- Entity scope: `resolved_order_ids` ⊆ candidate; placeholder nằm trong `rejected_candidates`.
- Evidence ownership: mọi `evidence_ref` trong output đều do gateway trả về trong case này.
- Claim linkage: mỗi claim có verdict + confidence + evidence_refs.
- Timeline: `shipment_analysis.timeline_complete` khớp sự hiện diện `delivered_customer_at`.
- Payment/refund totals: `captured_total_brl` = tổng event `captured` confirmed; `refundable_total_brl` = `refund_brl` của policy; `refunded_total_brl` = tổng refund completed.
- Source precedence: `selected_source` ∈ `sources` của từng conflict.
- Responsibility/action consistency: `financial_resolution` khớp `case_status` và `recommended_action` của policy.
- Confidence bounds: `0 ≤ confidence ≤ 1` ở mọi chỗ.

## 7. Reproducibility

- Runtime: Python ≥ 3.11, dependency pin trong `pyproject.toml` (`mcp>=2,<3`, `httpx2>=2,<3`, `jsonschema[format]>=4.25,<5`, `python-dotenv`).
- Không dùng LLM tại runtime; workflow là logic xác định, không random seed.
- Concurrency limit: 1 (chạy tuần tự 100 case).
- Lệnh chạy: `day09 run` → `day09 validate` → `day09 package --output dist/submission.zip`.
- Giới hạn tài nguyên: submission ≤ 12 MB, mỗi file ≤ 1 MB.
- Không ghi API key.
