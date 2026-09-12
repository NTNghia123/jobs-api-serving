# Migration Report — Phase 1: GCP Foundation

## 1. Tổng quan

Phase 1 dựng **nền móng hạ tầng GCP** cho luồng dữ liệu và Serving API mục tiêu:

```text
MongoDB + scraper + Dagster (VM)
              │
              │ sa-dagster-elt ghi dữ liệu — Phase 2
              ▼
  BigQuery jobs_staging / jobs_prod
              │
              │ sa-api-reader-{env} chỉ đọc — Phase 3
              ▼
      Jobs Serving API (Cloud Run) — Phase 6
              │
              ├── Secret Manager: API key + page-token secret
              └── Memorystore Redis: cache + rate limit dùng chung

GitHub Actions ── WIF/OIDC ──► sa-ci-deployer-staging ──► Cloud Run staging
```

Mục tiêu của phase này không phải đưa dữ liệu Mongo vào BigQuery hay deploy API, mà là chuẩn
bị sẵn các tài nguyên, danh tính và rào chắn chi phí để các phase sau có thể triển khai an toàn.

### Trạng thái

| Phạm vi | Trạng thái | Ý nghĩa |
| --- | --- | --- |
| Mã hạ tầng trong repo | **Hoàn thành** | Đã có bộ script `gcloud`/`bq` idempotent trong `infra/gcp/` |
| Thiết kế kiến trúc | **Đã chấp nhận** | Quyết định được ghi tại `docs/adr/ADR-022-deployment-cloud-run-wif.md` |
| Tài nguyên trên GCP | **Cần xác minh theo project** | Repo không chứa `config.sh`, output chạy script hay inventory của một project GCP cụ thể |
| Redis | **Tuỳ chọn, mặc định chưa tạo** | Chỉ được tạo khi truyền `CONFIRM_REDIS=1` vì phát sinh chi phí lúc idle |

> “Phase 1 hoàn thành” trong repo nghĩa là **hạ tầng đã được mô tả và tự động hoá đầy đủ**.
> Nó không tự động khẳng định một project GCP cụ thể đã được provision thành công.

---

## 2. Phase 1 giải quyết vấn đề gì?

Nếu API, ELT và CI dùng chung một tài khoản có quyền rộng, một lỗi cấu hình ở staging có thể đọc
secret hoặc dữ liệu production. Nếu CI dùng JSON service-account key, credential dài hạn có thể bị
lộ trong máy cá nhân hoặc GitHub. Nếu Redis và Cloud Run không có rào chắn, trial credit có thể bị
tiêu thụ ngoài ý muốn.

Phase 1 xử lý các rủi ro đó bằng bốn nguyên tắc:

1. **Tách môi trường bằng IAM:** staging và prod có dataset, secret và runtime service account riêng.
2. **Least privilege:** API chỉ đọc; ELT mới được ghi; CI chỉ deploy và chỉ được `actAs` đúng runtime SA.
3. **Không dùng JSON key:** GitHub Actions đổi OIDC token ngắn hạn qua Workload Identity Federation.
4. **Kiểm soát chi phí:** budget alert, cleanup image, giới hạn dự kiến cho BigQuery/Cloud Run và Redis
   có bước xác nhận riêng.

---

## 3. Tài nguyên được chuẩn bị

### 3.1. BigQuery

Hai dataset nằm trong cùng project và cùng location:

| Dataset mặc định | Mục đích | Quyền đọc | Quyền ghi |
| --- | --- | --- | --- |
| `jobs_staging` | Tích hợp và kiểm thử | `sa-api-reader-staging` | `sa-dagster-elt` |
| `jobs_prod` | Dữ liệu phục vụ production | `sa-api-reader-prod` | `sa-dagster-elt` |

Phase 1 chỉ tạo **dataset rỗng**. Các bảng silver, gold, quarantine, candidate,
`warehouse_batches` và `warehouse_state` thuộc Phase 2.

Location mặc định là `asia-southeast1` (Singapore). Location của BigQuery dataset không thể đổi
tại chỗ sau khi tạo, nên phải chốt `BQ_LOCATION` trước khi chạy script tạo dataset.

### 3.2. Service accounts và IAM

| Service account | Vai trò | Quyền chính |
| --- | --- | --- |
| `sa-api-reader-staging` | Cloud Run runtime staging | Đọc `jobs_staging`, chạy BigQuery job, đọc secret staging |
| `sa-api-reader-prod` | Cloud Run runtime prod | Đọc `jobs_prod`, chạy BigQuery job, đọc secret prod |
| `sa-dagster-elt` | Writer của pipeline ELT | Ghi hai dataset và chạy BigQuery job |
| `sa-ci-deployer-staging` | CI deploy staging | Ghi image, deploy Cloud Run, `actAs` reader staging |
| `sa-ci-deployer-prod` | Deploy prod thủ công | Ghi image, deploy Cloud Run, `actAs` reader prod |

Điểm quan trọng là quyền đọc dữ liệu được gán ở **cấp dataset**, không gán rộng ở cấp project.
Nhờ đó runtime staging không đọc được dataset prod và runtime prod cũng không dùng secret staging.

`sa-dagster-elt` được phép ghi cả staging và prod theo thiết kế hiện tại. Phase 2 bổ sung **writer
guard ở tầng ứng dụng** để yêu cầu chọn environment rõ ràng và chặn dataset tuỳ ý.

### 3.3. Artifact Registry

Repository Docker mặc định là `jobs-serving` tại cùng region. Hai CI deployer có quyền writer ở
phạm vi repository. Cleanup policy:

- xoá image không còn tag sau 7 ngày;
- giữ 10 phiên bản có tag gần nhất.

Phase 1 chỉ tạo kho image rỗng; build và push image theo commit SHA thuộc Phase 6.

### 3.4. Secret Manager

Bốn secret container được tách theo môi trường:

- `jobs-api-keys-staging`;
- `jobs-api-keys-prod`;
- `jobs-api-page-token-secret-staging`;
- `jobs-api-page-token-secret-prod`.

Page-token secret được sinh ngẫu nhiên một lần. Chạy lại script không tự xoay khoá, tránh làm hỏng
token đang còn hiệu lực. Hai API-key secret ban đầu **chưa có version dữ liệu**; phải dùng
`scripts/issue_key.py` để sinh key/hash rồi nạp version trước khi deploy.

Secret không được ghi vào `config.sh`, source code, Docker image hoặc GitHub secret dưới dạng JSON
service-account key.

### 3.5. Workload Identity Federation

Luồng xác thực staging:

```text
GitHub Actions
  └── OIDC token có claim repository
        └── WIF provider chỉ chấp nhận đúng GITHUB_REPO
              └── impersonate sa-ci-deployer-staging
                    ├── push image vào Artifact Registry
                    └── deploy Cloud Run bằng sa-api-reader-staging
```

CI không giữ credential GCP dài hạn. Theo quyết định hiện tại, WIF chỉ nối với staging; prod được
deploy thủ công để giữ một điểm kiểm soát con người trước khi có promotion/approval pipeline.

### 3.6. Budget và Memorystore Redis

Budget mặc định là `50 USD/tháng`, cảnh báo ở 50%, 80% và 100%. Budget **chỉ cảnh báo**, không tự
ngắt dịch vụ hoặc chặn chi tiêu.

Redis dùng làm cache và rate limiter dùng chung khi Cloud Run có nhiều instance. Đây là tài nguyên
có phí kể cả khi không có request, nên script chỉ tạo khi người chạy xác nhận rõ:

```bash
CONFIRM_REDIS=1 bash 60-networking-redis.sh
```

Khi hết demo, xoá Redis bằng:

```bash
bash 99-teardown.sh redis
```

Direct VPC egress từ Cloud Run tới private IP của Redis được cấu hình lúc deploy ở Phase 6, không
phải trong Phase 1.

---

## 4. Cấu trúc file Phase 1

| File | Chức năng |
| --- | --- |
| `infra/gcp/config.example.sh` | Mẫu cấu hình project, region, tên tài nguyên và cost guard |
| `infra/gcp/lib.sh` | Nạp/kiểm tra config, chọn đúng project và cung cấp helper dùng chung |
| `infra/gcp/00-preflight.sh` | Kiểm tra CLI, tài khoản, project và billing; không tạo tài nguyên |
| `infra/gcp/01-enable-apis.sh` | Bật các Google APIs cần cho toàn lộ trình |
| `infra/gcp/10-bigquery-datasets.sh` | Tạo dataset staging và prod |
| `infra/gcp/20-service-accounts.sh` | Tạo 5 service account, không tạo JSON key |
| `infra/gcp/21-iam-bindings.sh` | Gán quyền dataset/project và quyền `actAs` |
| `infra/gcp/30-artifact-registry.sh` | Tạo Docker repository, cleanup policy và quyền push image |
| `infra/gcp/40-secrets.sh` | Tạo secret, seed page-token và gán quyền đọc theo môi trường |
| `infra/gcp/50-wif.sh` | Tạo WIF pool/provider cho GitHub Actions staging |
| `infra/gcp/60-networking-redis.sh` | Lập kế hoạch hoặc tạo Redis khi được xác nhận |
| `infra/gcp/70-budget.sh` | Tạo budget alert 50/80/100% |
| `infra/gcp/99-teardown.sh` | Xem kế hoạch dọn, xoá Redis hoặc dataset staging có guard |

Tất cả script tạo tài nguyên đều được thiết kế **idempotent**: nếu tài nguyên đã tồn tại thì bỏ qua
hoặc áp lại policy/binding mong muốn. Idempotent không có nghĩa là mọi thuộc tính cũ sẽ được sửa;
ví dụ location của dataset đã tạo không thể đổi và script sẽ cảnh báo.

---

## 5. Cách chạy Phase 1

Khuyến nghị chạy trong Google Cloud Shell vì đã có sẵn `gcloud`, `bq`, Bash và Python 3.

### Bước 1 — Chuẩn bị project

1. Tạo hoặc chọn một GCP project.
2. Liên kết billing account.
3. Mở Cloud Shell, clone repo và vào thư mục hạ tầng.

```bash
git clone <URL_REPO_CUA_BAN>
cd jobs-serving-api/infra/gcp
```

### Bước 2 — Tạo cấu hình cá nhân

```bash
cp config.example.sh config.sh
nano config.sh
```

Tối thiểu phải thay:

```bash
export PROJECT_ID="project-id-thuc-te"
export GITHUB_REPO="owner/repository"
```

`config.sh` đã được ignore và không được commit. Kiểm tra kỹ `REGION`, `BQ_LOCATION`, budget và tên
tài nguyên trước lần chạy đầu tiên.

### Bước 3 — Chạy theo thứ tự

```bash
bash 00-preflight.sh
bash 01-enable-apis.sh
bash 10-bigquery-datasets.sh
bash 20-service-accounts.sh
bash 21-iam-bindings.sh
bash 30-artifact-registry.sh
bash 40-secrets.sh
bash 50-wif.sh
bash 70-budget.sh
```

Chỉ tạo Redis khi thật sự cần tích hợp/demo:

```bash
CONFIRM_REDIS=1 bash 60-networking-redis.sh
```

Mỗi script đều in `PROJECT`, `REGION` và tên script ở đầu. Dừng lại nếu target không đúng.

---

## 6. Cách xác minh kết quả

Nạp lại cấu hình vào phiên Cloud Shell hiện tại trước khi chạy các lệnh kiểm tra:

```bash
source ./config.sh
```

### 6.1. Kiểm tra dataset

```bash
bq --project_id="$PROJECT_ID" show --dataset "$PROJECT_ID:$DATASET_STAGING"
bq --project_id="$PROJECT_ID" show --dataset "$PROJECT_ID:$DATASET_PROD"
```

Kỳ vọng: cả hai dataset tồn tại và có location đúng với `BQ_LOCATION`.

### 6.2. Kiểm tra service accounts

```bash
gcloud iam service-accounts list \
  --project "$PROJECT_ID" \
  --filter="email:sa-*"
```

Kỳ vọng: có đủ 5 service account trong bảng ở mục 3.2 và không có user-managed JSON key do các
script Phase 1 tạo ra.

### 6.3. Kiểm tra Artifact Registry và cleanup policy

```bash
gcloud artifacts repositories describe "$AR_REPO" \
  --location "$REGION" \
  --project "$PROJECT_ID"
```

Kỳ vọng: repository có format Docker và có hai cleanup policy `delete-untagged`,
`keep-recent-tagged`.

### 6.4. Kiểm tra secrets

```bash
gcloud secrets list --project "$PROJECT_ID" --filter="name:jobs-api"
gcloud secrets versions list "$SECRET_PAGE_TOKEN_STAGING" --project "$PROJECT_ID"
gcloud secrets versions list "$SECRET_PAGE_TOKEN_PROD" --project "$PROJECT_ID"
```

Kỳ vọng: có 4 secret container; mỗi page-token secret có ít nhất một version enabled. Không in giá
trị secret ra terminal hoặc đưa giá trị đó vào báo cáo.

### 6.5. Kiểm tra WIF

```bash
gcloud iam workload-identity-pools providers describe github-provider \
  --workload-identity-pool github-pool \
  --location global \
  --project "$PROJECT_ID"
```

Kỳ vọng: provider enabled và `attributeCondition` chỉ chấp nhận đúng `GITHUB_REPO`.

### 6.6. Kiểm tra budget và Redis

```bash
BILLING_ACCOUNT="$(gcloud billing projects describe "$PROJECT_ID" \
  --format='value(billingAccountName)')"
gcloud billing budgets list --billing-account "$BILLING_ACCOUNT"
gcloud redis instances list --region "$REGION" --project "$PROJECT_ID"
```

Redis không xuất hiện vẫn là kết quả hợp lệ nếu chưa tới lúc demo. Budget cần hiện ngưỡng tháng và
các mức cảnh báo đã cấu hình.

---

## 7. Definition of Done

### Source code và thiết kế

- [x] Có script preflight và cấu hình mẫu không chứa secret.
- [x] Có script bật API, tạo hai BigQuery dataset cùng region.
- [x] Có 5 service account tách theo vai trò và môi trường.
- [x] IAM reader được giới hạn ở dataset/secret đúng môi trường.
- [x] CI staging dùng WIF/OIDC, không dùng JSON key.
- [x] Có Artifact Registry cleanup policy.
- [x] Có budget alert và biến cost guard cho các phase sau.
- [x] Redis cần xác nhận rõ trước khi tạo và có teardown riêng.
- [x] Quyết định Cloud Run/WIF/IAM được ghi trong ADR-022.

### Trên project GCP cụ thể

- [ ] `00-preflight.sh` chạy thành công.
- [ ] Các script bắt buộc từ `01` đến `70` chạy thành công.
- [ ] Inventory GCP khớp tài nguyên và ma trận quyền trong README này.
- [ ] API-key secret đã có version hợp lệ trước lần deploy tương ứng.
- [ ] WIF staging được thử bằng một GitHub Actions workflow tối thiểu hoặc ở Phase 6.
- [ ] Nếu đã tạo Redis, đã ghi nhận private endpoint và kế hoạch teardown.

Không đánh dấu phần này hoàn thành chỉ dựa trên việc script tồn tại trong git.

---

## 8. Những gì cố ý chưa làm

- Không tạo bảng hay nạp dữ liệu Mongo vào BigQuery — **Phase 2**.
- Không có BigQuery read adapter trong API — **Phase 3**.
- Chưa khôi phục DuckDB parity — **Phase 4**.
- Chưa nối Dagster với crawler/ELT theo batch — **Phase 5**.
- Chưa build/push image hoặc deploy Cloud Run — **Phase 6**.
- Chưa cấu hình Direct VPC egress trên Cloud Run — **Phase 6**.
- Chưa dùng Terraform, promotion/canary/rollback tự động hoặc tách staging/prod thành hai project —
  các cải tiến sau giai đoạn thử nghiệm.

---

## 9. Bàn giao sang Phase 2

Phase 2 có thể bắt đầu khi project đích đã vượt qua checklist ở mục 7. Đầu vào quan trọng nhất cho
ELT là:

- `PROJECT_ID`, `BQ_LOCATION`, `DATASET_STAGING`, `DATASET_PROD`;
- danh tính `sa-dagster-elt` gắn cho trusted VM;
- quyền `dataEditor` ở dataset và `jobUser` ở project;
- `BQ_MAX_BYTES_BILLED` làm cost guard;
- quy tắc writer guard: staging không dùng prod credentials, ghi prod phải chọn `--environment prod`.

Tài liệu liên quan:

- Hướng dẫn chạy script: [`infra/gcp/README.md`](../../infra/gcp/README.md)
- Quyết định Cloud Run, IAM và WIF: [`ADR-022`](../../docs/adr/ADR-022-deployment-cloud-run-wif.md)
- Kế hoạch migration đầy đủ: [`migration-mongo-bigquery-plan.md`](../../docs/migration-mongo-bigquery-plan.md)
- Báo cáo Phase 0: [`migrate-report/README.md`](../README.md)
