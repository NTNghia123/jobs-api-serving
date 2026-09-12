# ADR-022: Deployment — Cloud Run + service identity, WIF, SA tách môi trường

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-12
- Người quyết định: [Điền tên]
- Liên quan: ADR-018 (BigQuery), ADR-012/013 (auth, rate-limit), `docs/migration-mongo-bigquery-plan.md` §Phase 1/6; script `infra/gcp/`

## Bối cảnh
Serving API cần lên cloud phục vụ dữ liệu thật. Ràng buộc: giai đoạn thử nghiệm dùng **trial
credit** (kiểm soát chi phí chặt), team nhỏ (ưu tiên vận hành đơn giản), yêu cầu bảo mật cơ bản
(không rải JSON key, tách quyền staging/prod). Curriculum mentor: BigQuery + Cloud Run.

## Quyết định
- **Serving API chạy Cloud Run trực tiếp**, region `asia-southeast1` (gần VN; BigQuery/Cloud Run/
  Memorystore cùng region). Cloud Run public hạ tầng; `/v1/*` bắt buộc `X-API-Key`, `/health` mở.
- **Service identity tách môi trường (IAM enforce, không chỉ quy ước):**
  `sa-api-reader-{staging,prod}` (dataViewer **chỉ dataset môi trường** + jobUser project +
  secretAccessor **chỉ secret môi trường**), `sa-dagster-elt` (dataEditor + jobUser),
  `sa-ci-deployer-{staging,prod}` (run.developer + artifactregistry.writer repo-scoped +
  serviceAccountUser trên đúng runtime SA). Cloud Run chạy dưới danh tính `sa-api-reader-{env}`.
- **CI = GitHub Actions + Workload Identity Federation, KHÔNG JSON key.** WIF provider giới hạn
  `assertion.repository == <repo>`; chỉ **staging** deploy tự động, **prod** deploy thủ công.
- **1 project, 2 dataset** (`jobs_staging`/`jobs_prod`) — tách môi trường bằng dataset + IAM, không
  bằng project (gọn cho giai đoạn thử nghiệm).
- **VM = trusted compute boundary:** Mongo + scraper + Dagster + ELT chạy trên VM, gắn thẳng
  `sa-dagster-elt` (bỏ impersonation — VM đã là ranh giới tin cậy).
- **Cost guard [NGAY]:** budget alert (50/80/100%); `--maximum_bytes_billed` mỗi query BigQuery;
  Cloud Run `max-instances` trần + CPU/mem/concurrency; Memorystore **script sẵn nhưng chưa chạy**
  (tốn phí idle) + teardown script; Artifact Registry cleanup policy.
- **Hạ tầng = script `gcloud` idempotent** commit trong `infra/gcp/` (Terraform để [SAU]).

## Phương án đã cân nhắc
- **JSON key cho CI** — rủi ro lộ/khó xoay. Bỏ, dùng WIF (OIDC ngắn hạn).
- **2 project riêng staging/prod** — cô lập mạnh hơn nhưng phức tạp/tốn hơn cho giai đoạn thử nghiệm.
  Chọn 1 project + IAM cấp dataset/secret; nâng cấp sau nếu cần.
- **Impersonation từ VM tới writer SA** — thêm tầng nhưng VM đã là boundary tin cậy → gắn thẳng SA.
- **Terraform ngay** — chuẩn hơn nhưng dốc học cho giai đoạn này; script gcloud idempotent đủ dùng,
  Terraform để [SAU].
- **GKE / VM cho API** — thừa vận hành; Cloud Run scale-to-zero hợp chi phí + tải thử nghiệm.

## Hệ quả
- Tích cực: không JSON key; staging không đọc được prod (data + secret); chi phí có trần + cảnh báo;
  hạ tầng tái lập được (script idempotent); deploy prod có kiểm soát (thủ công).
- Đánh đổi: 1 project → cách ly yếu hơn 2 project; script gcloud kém khai báo hơn Terraform; prod
  deploy thủ công (chưa promotion/canary/rollback tự động — để [SAU]); Memorystore phải nhớ teardown.
