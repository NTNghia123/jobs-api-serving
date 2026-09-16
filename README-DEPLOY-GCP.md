# Hướng dẫn triển khai Jobs Serving Platform từ đầu đến cuối

Tài liệu này dành cho người mới bắt đầu với GCP. Điểm xuất phát được giả định là:

- đã có tài khoản GCP Trial và một project trống;
- hai repo đã có code trên remote, branch cần triển khai là `GCP_Deploy`;
- chưa chạy migration MongoDB → BigQuery, chưa deploy VM/Cloud Run, chưa tạo Looker Studio;
- cần triển khai cả Tuần 8: OpenTelemetry → Google Telemetry API → Cloud Trace.

Ngày đối chiếu runbook: **2026-09-15**.

## 1. Kết quả cuối cùng và kiến trúc

Sau khi hoàn thành, hệ thống có luồng sau:

```text
TopDev + VietnamWorks
        │
        ▼
job-scraper-1 trên Compute Engine VM
        │
        ├── MongoDB container, chỉ nghe 127.0.0.1:27017
        └── Dagster: crawl → ELT theo lịch
                         │
                         ▼
              BigQuery jobs_staging / jobs_prod
                         │
                         ▼
GitHub Actions ──WIF──► Artifact Registry ──► Cloud Run
                                                   │
                  Secret Manager ──────────────────┤
                  Telemetry API → Cloud Trace ◄────┤
                  Cloud Logging → BigQuery ◄───────┘
                                      │
                                      ▼
                         jobs_reporting → Looker Studio
```

Hai repo có vai trò khác nhau:

| Repo | Remote và branch | Vai trò |
| --- | --- | --- |
| Serving API | `https://github.com/NTNghia123/jobs-api-serving.git`, branch `GCP_Deploy` | ELT Python, FastAPI, script GCP, CI/CD, reporting views, OpenTelemetry |
| Scraper | `https://gitlab.iviec.vn/iviec-fim/iviec-data-science/job-scraper`, branch `GCP_Deploy` | Crawler, MongoDB, Dagster, systemd, backup/restore |

> Tên remote là `jobs-api-serving`. Runbook dùng hai đường dẫn có chủ đích:
> **Cloud Shell** clone tại `~/jobs-api-serving`, còn **VM** clone tại
> `/opt/jobs-serving-api`. Trên máy local, tên thư mục có thể tuỳ ý; hãy theo đúng nhãn môi trường
> của từng khối lệnh và không dùng lẫn hai đường dẫn.

## 2. Quy ước: chạy lệnh ở đúng nơi

Mỗi khối lệnh trong tài liệu có một nhãn:

- **`[Cloud Shell]`**: terminal trên Google Cloud Console; dùng để tạo hạ tầng và deploy.
- **`[VM]`**: terminal sau khi SSH vào `jobs-ops-vm`; dùng cho MongoDB, scraper và Dagster.
- **`[Máy local]`**: PowerShell/terminal trên máy cá nhân; chủ yếu dùng Git hoặc SSH tunnel.
- **`[GitHub UI]`**, **`[GCP Console]`**, **`[Looker Studio]`**: thao tác bằng giao diện web.

Đừng chạy lẫn lệnh `[Cloud Shell]` và `[VM]`. Có thể nhận biết qua prompt:

```text
# Cloud Shell thường giống:
tuannghianguyen161@cloudshell:~/jobs-api-serving (jobs-serving-platform)$

# VM thường giống:
tuannghianguyen161@jobs-ops-vm:/opt/job-scraper-1$
```

## 3. Các lỗi cũ đã được sửa trong runbook này

| Lỗi/nhầm lẫn trước đây | Cách đúng |
| --- | --- |
| Clone branch `looker-ready` | Dùng branch `GCP_Deploy`; repo này không có branch `looker-ready` |
| Dùng `bq --format="value(datasetId)"` hoặc `value(tableId)` | `bq` chỉ nhận `pretty`, `prettyjson`, `json`, `csv`, `sparse`, `none`; runbook dùng `--format=pretty`/`prettyjson` |
| Nghĩ dataset đã có nghĩa là đã có column | Dataset chỉ là container. Column thuộc **table/view**, và chỉ xuất hiện sau khi ELT hoặc script reporting tạo table/view |
| Đưa JSON vào phần prompt của `read -p` | Prompt chỉ là lời nhắc. Chạy lệnh trước, sau đó mới dán **nguyên dòng JSON** |
| Chỉ dán chuỗi hash vào Secret Manager | Secret API key cần cả object JSON, gồm `client_id`, `key_sha256`, `expires_at` |
| Dùng hash làm `SMOKE_API_KEY` | `SMOKE_API_KEY` là **key thô**; Secret Manager lưu **hash** của key đó |
| SSH lần đầu thấy cảnh báo chưa có key | Bình thường. Chấp nhận tạo `~/.ssh/google_compute_engine`; khi prompt đổi thành `...@jobs-ops-vm` là đã SSH thành công |
| Clone trên VM nhưng `git: command not found` | Cài `git` trước khi clone |
| Clone trực tiếp vào `/opt/...` bị `Permission denied` | Tạo thư mục bằng `sudo install -d` và giao ownership cho user trước |
| Dùng password tài khoản cho GitHub/GitLab | GitHub dùng PAT/`gh auth login`; GitLab private dùng Personal Access Token ở ô password |
| Tạo `.venv` trong một thư mục chưa clone rồi chạy requirements | Xác minh có `.git`, `requirements.txt`, `app/` trước khi tạo venv |
| Chạy `python ...` trên VM và gặp `python: command not found` | Gọi thẳng `/opt/jobs-serving-api/.venv/bin/python` |
| `dagster instance migrate` báo `alembic_version has more than one head` | Với setup mới chưa cần giữ history: dừng service, **move** `DAGSTER_HOME` lỗi sang bản backup, tạo instance sạch; không xóa bừa một Alembic head |
| ELT báo `Query without FROM clause cannot have a WHERE clause` | Serving repo trên VM đang ở commit cũ có SQL bootstrap sai; cập nhật `GCP_Deploy` có `FROM (SELECT 1)` rồi chạy lại |
| Đặt `RESTORE_SA=<sa-backup-restore@...>` | Không dùng dấu `< >`; email đúng là `sa-backup-restore@PROJECT_ID.iam.gserviceaccount.com` |
| Chạy lệnh IAM quản trị trên VM bằng `sa-dagster-elt` | Chạy IAM trong Cloud Shell bằng tài khoản owner/admin; runtime SA cố ý không có quyền quản trị IAM |
| `git push` báo `Everything up-to-date` rồi mong có workflow mới | Không có push event mới nên không có workflow mới; re-run run cũ hoặc tạo commit mới |
| `gcloud auth configure-docker` in danh sách dài `credHelpers` | Đây là cảnh báo/thông tin về Docker config; dòng `Adding credentials for: asia-southeast1-docker.pkg.dev` nghĩa là cấu hình đã được thêm |
| `curl -I .../v2/` trả 405 và nghĩ registry hỏng | `-I` gửi HEAD; 405 nghĩa là endpoint đã trả lời nhưng không hỗ trợ HEAD. Đây không phải lỗi kết nối |
| Load test trả HTTP `000` | `000` không phải HTTP status; curl chưa nhận response, thường do URL/key rỗng, lệnh bị dán vỡ hoặc lỗi mạng |
| API trả “Thiếu header X-API-Key” dù có `-H` | Biến shell chứa key đang rỗng; curl thường bỏ header có giá trị rỗng |
| Chạy `bq ls jobs_prod_logs` không thấy table ngay | Logging sink không hồi tố; table chỉ xuất hiện sau log mới phù hợp filter và có thể chậm vài phút |
| SQL reporting lỗi ở `CAST(...) DIV 100` | BigQuery Standard SQL đúng là `DIV(CAST(... AS INT64), 100)` |
| SQL reporting lỗi `Unexpected keyword WINDOW` | Quote field reserved bằng backtick: `jsonPayload.\`window\`` và alias `AS \`window\`` |
| Cloud Trace REST trả quota-project 403 | Thêm header `x-goog-user-project: PROJECT_ID`, và bảo đảm Trace API/IAM đã bật |

Hai lỗi source đã được sửa cùng tài liệu này:

- `infra/gcp/10-bigquery-datasets.sh` dùng `bq --format=pretty`;
- `infra/gcp/config.example.sh` dùng đúng `GITHUB_REPO="NTNghia123/jobs-api-serving"`.

## 4. Giá trị chuẩn dùng trong tài liệu

```text
PROJECT_ID       = jobs-serving-platform
REGION           = asia-southeast1
BQ_LOCATION      = asia-southeast1
ZONE             = asia-southeast1-b
VM_NAME          = jobs-ops-vm
VM_MACHINE_TYPE  = e2-standard-2
VM_BOOT_DISK     = 30 GB pd-balanced
VM_IMAGE         = Ubuntu 24.04 LTS
VM_SERVICE_ACCOUNT = sa-dagster-elt@jobs-serving-platform.iam.gserviceaccount.com
```

`e2-standard-2` có 2 vCPU và 8 GB RAM, phù hợp cấu hình đã dùng cho MongoDB + crawler + Dagster.
Đây là VM có tính phí; hãy dừng VM khi không cần chạy pipeline.

## 5. Bước 0 — Tạo project GCP và bật billing

**Mục đích:** tạo ranh giới tài nguyên và cho phép project sử dụng các dịch vụ có tính phí. GCP
Trial vẫn yêu cầu liên kết billing account.

### 5.1 Thao tác trên GCP Console

1. Mở <https://console.cloud.google.com/>.
2. Chọn **New Project** và tạo project có ID `jobs-serving-platform`.
3. Mở **Billing** và liên kết billing account Trial với project.
4. Bấm biểu tượng **Activate Cloud Shell** (`>_`).

Project **Name** và **Project ID** có thể khác nhau. Mọi câu lệnh ở đây dùng Project ID.

### 5.2 Chọn project trong Cloud Shell

**`[Cloud Shell]`**

```bash
export PROJECT_ID="jobs-serving-platform"
gcloud config set project "$PROJECT_ID"
gcloud config get-value project
gcloud auth list --filter=status:ACTIVE --format='value(account)'
```

Giải thích từng lệnh:

- `export PROJECT_ID=...` tạo biến dùng lại trong phiên shell hiện tại.
- `gcloud config set project ...` đặt project mặc định cho các lệnh `gcloud` sau đó.
- `gcloud config get-value project` in lại project đang chọn để tránh tạo nhầm tài nguyên.
- `gcloud auth list ...` in tài khoản đang đăng nhập và đang active.

Biến `export` chỉ tồn tại trong phiên terminal hiện tại. Mở Cloud Shell mới thì phải `source`
config hoặc khai báo lại. Đây là lý do trước mỗi phiên SSH ta thường nạp lại cấu hình; không phải vì
VM yêu cầu các biến đó.

## 6. Bước 1 — Clone serving repo vào Cloud Shell

**Mục đích:** Cloud Shell cần source chứa các script hạ tầng, workflow, Dockerfile và ELT.

**`[Cloud Shell]`**

```bash
cd ~
git clone --branch GCP_Deploy --single-branch \
  https://github.com/NTNghia123/jobs-api-serving.git \
  jobs-api-serving
cd ~/jobs-api-serving
git branch --show-current
git rev-parse HEAD
```

Giải thích:

- `cd ~` về home của Cloud Shell, nơi user có quyền ghi.
- `git clone --branch ... --single-branch` chỉ tải branch `GCP_Deploy`, tiết kiệm thời gian/dung lượng.
- Tham số cuối `jobs-api-serving` là tên thư mục được tạo trong Cloud Shell.
- `git branch --show-current` phải in `GCP_Deploy`.
- `git rev-parse HEAD` in commit SHA đầy đủ; SHA này dùng làm tag image.

Nếu thư mục đã clone từ trước, không clone đè:

```bash
cd ~/jobs-api-serving
git fetch origin
git switch GCP_Deploy
git pull --ff-only origin GCP_Deploy
```

- `git fetch origin` tải metadata/commit mới nhưng chưa sửa working tree.
- `git switch` chuyển đúng branch.
- `git pull --ff-only` chỉ cập nhật kiểu fast-forward, tránh tự tạo merge commit ngoài ý muốn.

## 7. Bước 2 — Tạo cấu hình hạ tầng cá nhân

**Mục đích:** mọi script dùng chung một nguồn cấu hình và luôn in project/region trước khi thay đổi
tài nguyên.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
cp config.example.sh config.sh
nano config.sh
```

Giải thích:

- `cd` chuyển vào thư mục chứa script GCP.
- `cp` tạo bản cấu hình cá nhân từ file mẫu; `config.sh` đã được Git ignore.
- `nano` mở editor trong terminal. Nhấn `Ctrl+O`, Enter để lưu; `Ctrl+X` để thoát.

Trong `config.sh`, tối thiểu phải có:

```bash
export PROJECT_ID="jobs-serving-platform"
export REGION="asia-southeast1"
export BQ_LOCATION="asia-southeast1"
export GITHUB_REPO="NTNghia123/jobs-api-serving"
```

Sau khi lưu:

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
printf 'PROJECT=%s\nREGION=%s\nGITHUB_REPO=%s\n' \
  "$PROJECT_ID" "$REGION" "$GITHUB_REPO"
```

- `source` chạy file trong shell hiện tại để các biến `export` có hiệu lực.
- `printf` kiểm tra ba giá trị quan trọng mà không phụ thuộc format của công cụ khác.

## 8. Bước 3 — Dựng nền tảng GCP

**Mục đích:** bật API, tạo BigQuery datasets, service accounts, IAM, Artifact Registry, secrets,
WIF và budget trước khi chạy workload.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
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

Giải thích từng lệnh:

- `00-preflight.sh`: chỉ đọc trạng thái; kiểm tra đăng nhập, project và billing.
- `01-enable-apis.sh`: bật BigQuery, Compute, Cloud Run, Artifact Registry, IAM, Secret Manager,
  Logging, Telemetry, Cloud Trace và các API còn lại. Chạy lại là an toàn.
- `10-bigquery-datasets.sh`: tạo `jobs_staging` và `jobs_prod` cùng location.
- `20-service-accounts.sh`: tạo năm SA chính; không tạo JSON key.
- `21-iam-bindings.sh`: gán quyền BigQuery, Cloud Run, `actAs`, và quyền ghi trace Tuần 8.
- `30-artifact-registry.sh`: tạo Docker repository `jobs-serving`, cleanup policy và quyền CI push.
- `40-secrets.sh`: tạo bốn secret container, seed hai page-token secret và gán IAM.
- `50-wif.sh`: cho token OIDC từ đúng GitHub repo impersonate CI staging SA.
- `70-budget.sh`: tạo cảnh báo 50/80/100%; budget không tự tắt dịch vụ.

Các script được thiết kế idempotent: có tài nguyên rồi thì bỏ qua hoặc đồng bộ policy. Tuy nhiên,
location BigQuery đã tạo không đổi được; nếu script báo location khác thì phải dừng và quyết định tạo
dataset mới.

### 8.1 Kiểm dataset và schema đúng cách

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
bq --project_id="$PROJECT_ID" ls --datasets --format=pretty
bq --project_id="$PROJECT_ID" ls --format=pretty "$PROJECT_ID:$DATASET_STAGING"
```

- Lệnh đầu liệt kê **dataset**.
- Lệnh hai liệt kê **table/view trong dataset staging**. Ngay sau bước foundation, danh sách rỗng là
  đúng vì ELT chưa chạy.

Sau khi ELT chạy, xem column của một table bằng:

```bash
bq --project_id="$PROJECT_ID" show --schema --format=prettyjson \
  "$PROJECT_ID:$DATASET_STAGING.silver_jobs"
```

- `show --schema` đọc schema của **table** `silver_jobs`.
- `--format=prettyjson` là format hợp lệ của `bq` và dễ đọc tên/type/mode của column.

## 9. Bước 4 — Phát API key và nạp Secret Manager

**Mục đích:** `/v1/*` dùng API key. Server chỉ lưu SHA-256; key thô được đưa cho client và cho smoke
test. Nên phát key riêng cho staging và production.

### 9.1 Phát key staging

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving
python3 -m scripts.issue_key team-ai-staging --days 90
```

- Script dùng thư viện chuẩn Python để sinh key ngẫu nhiên.
- `team-ai-staging` là `client_id` dùng trong log/rate limit.
- `--days 90` đặt hạn dùng 90 ngày; bỏ flag nếu muốn `expires_at=null`.
- Output có hai giá trị khác nhau: dòng `API key` là key thô; dòng JSON chứa hash.

Lưu key thô vào password manager. Không commit, không gửi vào chat/log và không làm mất ba ký tự cuối
trong giá trị thật. Nếu một chuỗi như `R5K20...` nằm ở dòng `API key` của script thì đó là raw key;
không thể kết luận chỉ dựa vào hình dạng chuỗi.

Nạp **nguyên dòng JSON** mà script in ra:

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
read -r -p 'Dán nguyên dòng JSON hash staging: ' STAGING_KEYS_JSON
printf '%s' "$STAGING_KEYS_JSON" | \
  gcloud secrets versions add "$SECRET_API_KEYS_STAGING" \
    --data-file=- \
    --project="$PROJECT_ID"
unset STAGING_KEYS_JSON
```

Giải thích:

- `read -r -p '... ' STAGING_KEYS_JSON` hiển thị lời nhắc rồi chờ bạn dán JSON. JSON không nằm bên
  trong phần prompt.
- Giá trị cần dán có dạng
  `{"team-ai-staging":{"key_sha256":"<64-ký-tự-hex>","expires_at":"..."}}`.
- `printf '%s'` gửi đúng JSON qua standard input, không tự thêm newline.
- `gcloud secrets versions add ... --data-file=-` đọc payload từ standard input và tạo version mới.
- `unset` xóa biến khỏi phiên shell sau khi dùng.

### 9.2 Phát và nạp key production

```bash
cd ~/jobs-api-serving
python3 -m scripts.issue_key team-ai-prod --days 90
source infra/gcp/config.sh
read -r -p 'Dán nguyên dòng JSON hash production: ' PROD_KEYS_JSON
printf '%s' "$PROD_KEYS_JSON" | \
  gcloud secrets versions add "$SECRET_API_KEYS_PROD" \
    --data-file=- \
    --project="$PROJECT_ID"
unset PROD_KEYS_JSON
```

Các lệnh giống staging nhưng dùng client và secret production. Lưu riêng raw production key.

Kiểm tra **metadata**, không in payload:

```bash
gcloud secrets versions list "$SECRET_API_KEYS_STAGING" --project="$PROJECT_ID"
gcloud secrets versions list "$SECRET_API_KEYS_PROD" --project="$PROJECT_ID"
```

Mỗi lệnh phải thấy ít nhất một version ở trạng thái `ENABLED`.

## 10. Bước 5 — Chuẩn bị GitHub Actions secrets

**Mục đích:** GitHub Actions dùng WIF thay cho JSON service-account key, build image, deploy staging
private và smoke qua identity token.

Lấy ba giá trị không nhạy cảm:

**`[Cloud Shell]`**

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
GCP_WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
GCP_DEPLOYER_SA="${SA_CI_DEPLOYER_STAGING}@${PROJECT_ID}.iam.gserviceaccount.com"
printf 'GCP_PROJECT_ID=%s\nGCP_WIF_PROVIDER=%s\nGCP_DEPLOYER_SA=%s\n' \
  "$PROJECT_ID" "$GCP_WIF_PROVIDER" "$GCP_DEPLOYER_SA"
```

- `gcloud projects describe` lấy project number; WIF dùng number, không dùng project ID ở resource name.
- Hai phép gán sau dựng đúng provider resource name và email CI SA.
- `printf` in các giá trị để copy sang GitHub.

**`[GitHub UI]`** vào repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Giá trị |
| --- | --- |
| `GCP_PROJECT_ID` | `jobs-serving-platform` |
| `GCP_WIF_PROVIDER` | giá trị vừa in |
| `GCP_DEPLOYER_SA` | email vừa in |
| `SMOKE_API_KEY` | **raw staging key**, không phải JSON/hash |

Không cần và không được tạo secret JSON service-account key.

## 11. Bước 6 — Tạo bucket backup trước khi dựng VM

**Mục đích:** tạo bucket GCS, lifecycle 30 ngày và quyền cho VM SA upload backup. Restore dùng SA
read-only riêng.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 80-backup-gcs.sh
```

- `source` nạp project/tên bucket/SA.
- Script tạo bucket `gs://jobs-serving-platform-mongo-backup`, cấp `objectCreator` + `objectViewer`
  cho `sa-dagster-elt`, và `objectViewer` cho `sa-backup-restore`.
- Writer cần `objectViewer` vì `gcloud storage cp` kiểm tra object trong quá trình upload; writer vẫn
  không có quyền delete.

## 12. Bước 7 — Tạo Compute Engine VM

**Mục đích:** tạo máy chạy MongoDB, crawler, Dagster và ELT; gắn trực tiếp `sa-dagster-elt` để Python
và gcloud dùng ADC từ metadata server.

**`[Cloud Shell]`**

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
export VM_NAME="jobs-ops-vm"
export ZONE="asia-southeast1-b"
export VM_SA="${SA_DAGSTER_ELT}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud compute instances create "$VM_NAME" \
  --project="$PROJECT_ID" \
  --zone="$ZONE" \
  --machine-type="e2-standard-2" \
  --image-family="ubuntu-2404-lts-amd64" \
  --image-project="ubuntu-os-cloud" \
  --boot-disk-size="30GB" \
  --boot-disk-type="pd-balanced" \
  --service-account="$VM_SA" \
  --scopes="https://www.googleapis.com/auth/cloud-platform"
```

Giải thích:

- Ba `export` đặt tên VM, zone và email SA cho phiên Cloud Shell hiện tại.
- `instances create` tạo và khởi động VM.
- `--machine-type` cấp 2 vCPU/8 GB RAM.
- `--image-family` và `--image-project` chọn Ubuntu 24.04 LTS chính chủ.
- Hai flag `--boot-disk-*` tạo ổ persistent balanced 30 GB.
- `--service-account` gắn identity ELT thay vì Compute Engine default SA.
- `--scopes=cloud-platform` cho token metadata có scope gọi Cloud APIs; IAM vẫn quyết định quyền thật.

Kiểm cấu hình sau khi tạo:

```bash
gcloud compute instances describe "$VM_NAME" \
  --project="$PROJECT_ID" \
  --zone="$ZONE" \
  --format='yaml(status,machineType,disks[0].diskSizeGb,serviceAccounts)'
```

`describe` chỉ đọc. Kỳ vọng `status: RUNNING` và email SA là `sa-dagster-elt@...`.

> Nếu VM đã tồn tại thì không chạy `instances create` lần nữa. Chỉ dùng `describe` để xác minh.

## 13. Bước 8 — SSH vào VM

**Mục đích:** mở shell trên máy vừa tạo. Không cần `source config.sh` ở bên trong VM chỉ để SSH;
config/biến chỉ cần ở terminal đang chạy lệnh `gcloud compute ssh`.

**`[Cloud Shell]`**

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
export VM_NAME="jobs-ops-vm"
export ZONE="asia-southeast1-b"
gcloud compute ssh "$VM_NAME" \
  --project="$PROJECT_ID" \
  --zone="$ZONE"
```

- `source` khôi phục `PROJECT_ID` khi mở phiên Cloud Shell mới.
- Hai `export` khôi phục biến VM vì shell mới không nhớ biến cũ.
- `gcloud compute ssh` tìm VM bằng project+zone rồi mở SSH.

Lần đầu, gcloud có thể hỏi tạo `~/.ssh/google_compute_engine`, passphrase và xác nhận host key. Đây là
bình thường. Khi thấy prompt `user@jobs-ops-vm:~$`, SSH đã thành công.

## 14. Bước 9 — Cài Git và clone cả hai repo vào VM

**Mục đích:** script provisioning nằm trong scraper repo; Dagster sẽ gọi Python từ serving repo.
Thiếu một trong hai repo thì pipeline không chạy.

### 14.1 Cài Git và chuẩn hóa timezone

**`[VM]`**

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates
sudo timedatectl set-timezone Asia/Ho_Chi_Minh
timedatectl
```

- `apt-get update` cập nhật danh sách package.
- `apt-get install` cài Git và CA certificates cho HTTPS clone.
- `timedatectl set-timezone` bảo đảm timer backup `03:30` thực sự là giờ Việt Nam.
- `timedatectl` in timezone hiện tại để xác minh.

### 14.2 Tạo thư mục có quyền ghi

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/job-scraper-1
sudo install -d -o "$USER" -g "$(id -gn)" /opt/jobs-serving-api
```

- `/opt` thuộc root nên user thường không tự tạo worktree được.
- `install -d` tạo directory nếu chưa có và đặt owner/group đúng user hiện tại.
- `$(id -gn)` lấy primary group của user, tránh hard-code tên group.

### 14.3 Clone serving repo đúng branch

```bash
git clone --branch GCP_Deploy --single-branch \
  https://github.com/NTNghia123/jobs-api-serving.git \
  /opt/jobs-serving-api
```

Lệnh clone vào directory rỗng đã có quyền ghi. Không dùng branch `looker-ready`.

### 14.4 Clone scraper repo private

```bash
git clone --branch GCP_Deploy --single-branch \
  https://gitlab.iviec.vn/iviec-fim/iviec-data-science/job-scraper \
  /opt/job-scraper-1
```

Khi GitLab hỏi:

- `Username`: username GitLab, ví dụ `NghiaNT118`;
- `Password`: dán **Personal Access Token** có scope `read_repository`, không dùng password tài khoản.

Không nhúng token trực tiếp vào URL vì token sẽ lưu trong shell history.

### 14.5 Xác minh hai clone

```bash
git -C /opt/jobs-serving-api branch --show-current
git -C /opt/job-scraper-1 branch --show-current
test -f /opt/jobs-serving-api/requirements.txt && echo "serving repo OK"
test -f /opt/job-scraper-1/deploy/provision-vm.sh && echo "scraper repo OK"
```

- `git -C` chạy Git trong directory chỉ định mà không cần `cd`.
- Cả hai branch phải là `GCP_Deploy`.
- Hai lệnh `test -f` bảo đảm đây là clone thật, không phải directory chỉ chứa `.venv`.

Nếu `/opt/jobs-serving-api` đã chứa `.venv` nhưng không có `.git`, giữ bản sao rồi clone lại:

```bash
BACKUP_DIR="/opt/jobs-serving-api.incomplete-$(date +%Y%m%d-%H%M%S)"
sudo mv /opt/jobs-serving-api "$BACKUP_DIR"
sudo install -d -o "$USER" -g "$(id -gn)" /opt/jobs-serving-api
git clone --branch GCP_Deploy --single-branch \
  https://github.com/NTNghia123/jobs-api-serving.git \
  /opt/jobs-serving-api
```

- `BACKUP_DIR=...` tạo tên có timestamp.
- `mv` giữ directory lỗi để có thể phục hồi, không xóa dữ liệu.
- `install -d` sửa đúng lỗi permission trước lần clone mới.

## 15. Bước 10 — Tạo virtual environment cho serving repo

**Mục đích:** Dagster trong scraper repo sẽ ưu tiên
`/opt/jobs-serving-api/.venv/bin/python`; đây là Python có `pymongo`, BigQuery và OpenTelemetry đúng
version của serving project.

**`[VM]`**

```bash
cd /opt/jobs-serving-api
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
```

- `python3 -m venv` tạo môi trường Python riêng trong repo.
- Hai lệnh `.venv/bin/python -m pip` luôn dùng đúng pip của venv, không phụ thuộc shell activation.
- `requirements.txt` đủ cho runtime/ELT. `requirements-dev.txt` chỉ cần khi chạy test/lint.
- `pip check` kiểm tra dependency đã cài không xung đột.

Nếu báo không có `requirements.txt` hoặc “not a git repository”, directory chưa được clone đúng; quay
lại Bước 9 thay vì tiếp tục tạo venv.

## 16. Bước 11 — Cấu hình scraper, MongoDB, ELT và backup

**Mục đích:** cung cấp một file `.env` dùng chung cho Docker Compose và systemd. File này chứa secret,
không commit.

**`[VM]`**

```bash
cd /opt/job-scraper-1
cp .env.example .env
openssl rand -hex 24
nano .env
```

- `cp` tạo file runtime từ mẫu.
- `openssl rand -hex 24` sinh password Mongo 48 ký tự hex, mạnh và không có ký tự cần URL-encode.
- Copy password rồi dùng cùng một giá trị ở `MONGO_ROOT_PASSWORD`, `MONGO_URI` và `JOBS_MONGO_URI`.
- `nano` mở file để sửa.

Các dòng bắt buộc trong `.env`:

```dotenv
MONGO_ROOT_USERNAME=root
MONGO_ROOT_PASSWORD=<PASSWORD_HEX_VUA_SINH>
MONGO_ENABLED=true
MONGO_URI=mongodb://root:<PASSWORD_HEX_VUA_SINH>@127.0.0.1:27017/?authSource=admin
MONGO_DATABASE=job_crawler

S3_ENABLED=false

SERVING_API_PROJECT_PATH=/opt/jobs-serving-api
SERVING_ELT_ENVIRONMENT=staging
JOBS_MONGO_URI=mongodb://root:<PASSWORD_HEX_VUA_SINH>@127.0.0.1:27017/?authSource=admin
JOBS_MONGO_DATABASE=job_crawler
JOBS_BQ_PROJECT=jobs-serving-platform
JOBS_BQ_DATASET_STAGING=jobs_staging
JOBS_BQ_DATASET_PROD=jobs_prod
JOBS_BQ_LOCATION=asia-southeast1
JOBS_BQ_MAX_BYTES_BILLED=2000000000

BACKUP_BUCKET=jobs-serving-platform-mongo-backup
MONGO_CONTAINER=job-crawler-mongo
RESTORE_DB=job_crawler_restore_test
```

Ý nghĩa:

- Nhóm `MONGO_*` được crawler và Docker Compose dùng.
- Nhóm `JOBS_MONGO_*`/`JOBS_BQ_*` được CLI ELT của serving repo dùng.
- `SERVING_API_PROJECT_PATH` cho Dagster biết repo/Python ELT nằm ở đâu.
- `SERVING_ELT_ENVIRONMENT=staging` giữ schedule ở staging trong giai đoạn kiểm thử.
- `BACKUP_BUCKET` phải khớp bucket Bước 6.
- Không để literal `<PASSWORD_HEX_VUA_SINH>` trong file; thay bằng password thật.

Khóa quyền đọc file:

```bash
chmod 600 /opt/job-scraper-1/.env
```

`chmod 600` chỉ cho owner đọc/ghi file secret.

## 17. Bước 12 — Provision MongoDB và Dagster

**Mục đích:** cài Docker, Node 20, pnpm, Google Cloud CLI, Python venv Dagster; sau đó bật Mongo,
Dagster webserver/daemon và backup timer bằng systemd.

**`[VM]`**

```bash
cd /opt/job-scraper-1
bash deploy/provision-vm.sh
```

- Script tự xác định repo root.
- Docker Compose chỉ expose Mongo ở `127.0.0.1:27017`.
- Dagster webserver chỉ nghe `127.0.0.1:3000`.
- Crawler dependency được cài bằng `pnpm install --frozen-lockfile`.
- Hai systemd service được enable để tự khởi động lại sau reboot.

Nếu Docker vừa được cài, đăng xuất rồi SSH lại để group `docker` có hiệu lực. Script dùng `sudo
docker` ở lần đầu nên vẫn có thể hoàn tất.

### 17.1 Migrate Dagster instance trước khi tin daemon

```bash
sudo systemctl stop dagster-webserver dagster-daemon
DAGSTER_HOME=/opt/job-scraper-1/orchestration/.dagster_home \
  /opt/job-scraper-1/orchestration/.venv/bin/dagster instance migrate
sudo systemctl start dagster-daemon dagster-webserver
```

- `stop` tránh hai process dùng SQLite metadata trong lúc migrate.
- `DAGSTER_HOME=... dagster instance migrate` nâng schema Dagster/Alembic của run/schedule storage.
  Với instance mới, lệnh là no-op hoặc tạo schema hiện hành.
- `start` bật daemon trước, rồi webserver.

Đây là cách xử lý lỗi daemon vẫn “active” nhưng log có `check_alembic_revision` hoặc
`migration_context.get_current_revision`. `active (running)` chỉ nói process đang sống tại thời điểm
kiểm tra, không chứng minh mọi vòng xử lý bên trong đều thành công.

Nếu chính lệnh migrate báo:

```text
alembic.util.exc.CommandError: Version table 'alembic_version' has more than one head present
```

thì metadata SQLite cũ đã ở trạng thái migration không nhất quán. Với lần setup mới, khi chưa có
Dagster run history cần giữ, cách an toàn là **di chuyển cả instance cũ sang backup**, không sửa/xóa
ngẫu nhiên một row trong `alembic_version`:

```bash
sudo systemctl stop dagster-webserver dagster-daemon
OLD_DAGSTER_HOME="/opt/job-scraper-1/orchestration/.dagster_home"
BROKEN_DAGSTER_HOME="${OLD_DAGSTER_HOME}.multihead-$(date +%Y%m%d-%H%M%S)"
mv "$OLD_DAGSTER_HOME" "$BROKEN_DAGSTER_HOME"
mkdir -p "$OLD_DAGSTER_HOME"
cp /opt/job-scraper-1/orchestration/dagster.yaml "$OLD_DAGSTER_HOME/dagster.yaml"
DAGSTER_HOME="$OLD_DAGSTER_HOME" \
  /opt/job-scraper-1/orchestration/.venv/bin/dagster instance migrate
sudo systemctl start dagster-daemon dagster-webserver
```

- Hai biến đặt đường dẫn instance lỗi và tên backup có timestamp.
- `mv` giữ nguyên toàn bộ SQLite/run metadata cũ để có thể điều tra, không xóa.
- `mkdir` + `cp` tạo `DAGSTER_HOME` sạch với cấu hình concurrency đúng.
- `instance migrate` khởi tạo schema hiện hành trên instance sạch.
- Sau đó schedule trở lại trạng thái mặc định `STOPPED`; bật lại ở Bước 15.

Nếu instance đã có run history quan trọng, không reset theo cách này. Giữ service dừng, sao lưu
directory và phân tích từng SQLite database/Alembic revision trước khi sửa.

### 17.2 Xác minh service

```bash
sudo systemctl status dagster-webserver --no-pager
sudo systemctl status dagster-daemon --no-pager
sudo systemctl list-timers mongo-backup --no-pager
cd /opt/job-scraper-1
docker compose -f docker-compose.mongo.yml --env-file .env ps
```

- Hai `status` phải hiện `active (running)` và không có lỗi mới.
- `list-timers` phải có lần chạy kế tiếp của `mongo-backup.timer` lúc 03:30 giờ VM.
- `docker compose ... ps` phải thấy `job-crawler-mongo` ở trạng thái `Up`, chỉ bind localhost.

Xem log gần nhất khi có lỗi:

```bash
sudo journalctl -u dagster-daemon -n 200 --no-pager
sudo journalctl -u dagster-webserver -n 100 --no-pager
sudo journalctl -u mongo-backup.service -n 100 --no-pager
```

`journalctl -u` lọc log theo unit; `-n` giới hạn số dòng; `--no-pager` in thẳng ra terminal.

## 18. Bước 13 — Chạy thử scraper và ELT

**Mục đích:** chứng minh Mongo có dữ liệu thật, mapping/quality checks đạt và BigQuery được publish
trước khi deploy API.

### 18.1 Kiểm MongoDB

**`[VM]`**

```bash
cd /opt/job-scraper-1
set -a
source .env
set +a
docker exec job-crawler-mongo mongosh \
  -u "$MONGO_ROOT_USERNAME" \
  -p "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --quiet \
  --eval 'db.getSiblingDB("job_crawler").jobs.countDocuments()'
```

- `set -a` làm các biến đọc từ `.env` tự động được export cho process con.
- `source .env` nạp cấu hình; `set +a` trả shell về chế độ bình thường.
- `docker exec ... mongosh` chạy Mongo shell trong container và đếm collection `jobs`.
- Nếu count là 0, chạy crawler/Dagster trước; ELT không thể tạo dashboard có dữ liệu từ Mongo rỗng.

Chạy một crawl thủ công hẹp để kiểm kết nối, ví dụ TopDev category `g1`:

```bash
cd /opt/job-scraper-1
pnpm start -- --platform topdev --category g1
```

- `pnpm start` chạy entrypoint TypeScript.
- Dấu `--` chuyển các flag sau nó cho ứng dụng crawler.
- `--platform` và `--category` giới hạn phạm vi test. Full daily job sẽ chạy toàn bộ category đã khai
  báo cho TopDev và VietnamWorks.

### 18.2 Dry-run ELT

```bash
cd /opt/job-scraper-1
set -a
source .env
set +a
cd /opt/jobs-serving-api
.venv/bin/python -m app.elt.serving.run_serving_elt \
  --environment staging \
  --batch-id phase2-dry-run \
  --dry-run
```

- Nạp `.env` scraper vì nó chứa URI Mongo và cấu hình BigQuery của ELT.
- Gọi thẳng `.venv/bin/python`; không dùng bare `python`.
- `--environment staging` chọn dataset allowlisted `jobs_staging`.
- `--batch-id` gắn định danh dễ nhận biết.
- `--dry-run` vẫn đọc Mongo, map, build gold và chạy quality checks nhưng không ghi BigQuery.

Kỳ vọng exit code 0 và quality report `OK`.

### 18.3 Publish staging

```bash
STAGING_BATCH="batch-staging-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python -m app.elt.serving.run_serving_elt \
  --environment staging \
  --batch-id "$STAGING_BATCH" \
  --crawl-batch-id "manual-bootstrap"
```

- `date -u` tạo timestamp UTC để batch ID không trùng.
- Không có `--dry-run`, nên ELT tạo table/candidate, chạy checks và publish atomically.
- `--crawl-batch-id` lưu lineage cho lần bootstrap thủ công.

Exit code: `0` thành công/no-op; `2` batch lịch sử đã publish; `3` quality gate fail; `1` lỗi cấu
hình/hạ tầng.

### 18.4 Publish production cho Looker Studio

Chỉ làm sau khi staging đạt:

```bash
PROD_BATCH="batch-prod-$(date -u +%Y%m%dT%H%M%SZ)"
.venv/bin/python -m app.elt.serving.run_serving_elt \
  --environment prod \
  --batch-id "$PROD_BATCH" \
  --crawl-batch-id "manual-prod-bootstrap"
```

- `--environment prod` là lựa chọn tường minh bắt buộc; writer guard không nhận `production`/`PROD`.
- Looker core views đọc `jobs_prod`, nên production phải có ít nhất một published batch trước Bước 22.

### 18.5 Xác minh table và column

**`[Cloud Shell]`** hoặc **`[VM]`**:

```bash
source ~/jobs-api-serving/infra/gcp/config.sh 2>/dev/null || true
bq --project_id="jobs-serving-platform" ls --format=pretty \
  "jobs-serving-platform:jobs_staging"
bq --project_id="jobs-serving-platform" show --schema --format=prettyjson \
  "jobs-serving-platform:jobs_staging.silver_jobs"
bq --project_id="jobs-serving-platform" query --use_legacy_sql=false \
  'SELECT * FROM `jobs-serving-platform.jobs_staging.warehouse_state`'
```

- `source ... || true` chỉ hữu ích ở Cloud Shell; trên VM file config có thể không tồn tại nên không
  để việc source làm dừng các lệnh hard-code phía sau.
- `bq ls` phải thấy các table warehouse.
- `show --schema` mới là lệnh xác nhận column.
- Query cuối phải có một pointer `published_batch_id`.

## 19. Bước 14 — Kiểm backup và restore

**Mục đích:** một backup chỉ có giá trị khi upload hoàn tất và đã restore thử được.

### 19.1 Backup thủ công

**`[VM]`**

```bash
cd /opt/job-scraper-1
bash deploy/backup.sh
gcloud storage ls -l "gs://jobs-serving-platform-mongo-backup/mongo/"
```

- `backup.sh` chạy `mongodump --archive --gzip`, kiểm file không rỗng rồi upload tên có timestamp.
- Chỉ dòng cuối `Backup xong: gs://...` chứng minh upload thành công; dòng `upload ...` chỉ báo bắt đầu.
- `gcloud storage ls -l` liệt kê object thật cùng size/time.

Nếu upload trả 403 `storage.objects.get`, quay lại **Cloud Shell** và chạy lại:

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 80-backup-gcs.sh
```

Không chạy lệnh IAM trên VM bằng `sa-dagster-elt`; SA đó không có quyền quản trị IAM.

### 19.2 Giá trị restore SA đúng

**`[Cloud Shell]`**

```bash
source ~/jobs-api-serving/infra/gcp/config.sh
RESTORE_SA="${SA_BACKUP_RESTORE}@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$RESTORE_SA" --project="$PROJECT_ID"
```

- Biến phải chứa email đầy đủ, không có dấu `< >`.
- `describe` xác nhận SA tồn tại trước khi cấp quyền impersonation.

Cho user đang đăng nhập quyền impersonate restore SA:

```bash
MY_ACCOUNT="$(gcloud config get-value account)"
gcloud iam service-accounts add-iam-policy-binding "$RESTORE_SA" \
  --project="$PROJECT_ID" \
  --member="user:${MY_ACCOUNT}" \
  --role="roles/iam.serviceAccountTokenCreator"
```

- `MY_ACCOUNT` lấy email user owner/admin hiện tại.
- Binding được gán cho **user**, không gán cho ELT writer; như vậy backup writer không tự biến thành
  backup reader.

Thực hiện restore theo `deploy/restore.sh` vào DB tạm. Nếu chạy script trên VM, dùng một cấu hình
gcloud tạm cho user để không thay active account mặc định của backup timer:

```bash
RESTORE_GCLOUD_CONFIG="$(mktemp -d)"
CLOUDSDK_CONFIG="$RESTORE_GCLOUD_CONFIG" gcloud auth login --no-launch-browser
CLOUDSDK_CONFIG="$RESTORE_GCLOUD_CONFIG" \
IMPERSONATE_SA="sa-backup-restore@jobs-serving-platform.iam.gserviceaccount.com" \
  bash /opt/job-scraper-1/deploy/restore.sh
rm -rf "$RESTORE_GCLOUD_CONFIG"
```

- `mktemp -d` tạo config directory tạm.
- `CLOUDSDK_CONFIG=... gcloud auth login` cô lập credential user khỏi config gcloud mà timer dùng.
- `IMPERSONATE_SA=... restore.sh` tải backup mới nhất bằng restore SA rồi restore vào
  `job_crawler_restore_test`, không đè `job_crawler`.
- `rm -rf` chỉ xóa directory tạm vừa tạo; không dùng biến này cho đường dẫn khác.

Đối chiếu document:

```bash
cd /opt/job-scraper-1
set -a; source .env; set +a
docker exec job-crawler-mongo mongosh \
  -u "$MONGO_ROOT_USERNAME" -p "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin --quiet \
  --eval 'print("prod:", db.getSiblingDB("job_crawler").jobs.countDocuments(), "restore:", db.getSiblingDB("job_crawler_restore_test").jobs.countDocuments())'
```

Hai count phải khớp với thời điểm backup hoặc có chênh lệch giải thích được nếu crawler đã ghi thêm.

## 20. Bước 15 — Bật Dagster schedule

**Mục đích:** sau khi manual crawl/ELT đạt, cho daemon tự chạy full crawl → ELT. Schedule mặc định
`STOPPED` để không vô tình chạy trước khi cấu hình xong.

**`[VM]`**

```bash
DAGSTER_HOME=/opt/job-scraper-1/orchestration/.dagster_home \
  /opt/job-scraper-1/orchestration/.venv/bin/dagster schedule start \
  daily_serving_batch_schedule \
  -w /opt/job-scraper-1/orchestration/workspace.yaml
```

- `DAGSTER_HOME` chọn đúng SQLite instance mà daemon/webserver dùng.
- `schedule start` bật `daily_serving_batch_schedule` lúc 02:00 Asia/Ho_Chi_Minh.
- `-w` nạp cùng workspace code location.

Trong giai đoạn thử nghiệm, `.env` để `SERVING_ELT_ENVIRONMENT=staging`. Khi quyết định daily batch
phải cập nhật production cho Looker, đổi thành `prod`, rồi restart daemon để EnvironmentFile được nạp
lại:

```bash
sudo nano /opt/job-scraper-1/.env
sudo systemctl restart dagster-daemon
```

Không đổi sang prod trước khi staging smoke và reconciliation đạt.

Mở Dagster UI qua tunnel. Chạy từ terminal có thể truy cập bằng trình duyệt của bạn:

```bash
gcloud compute ssh jobs-ops-vm \
  --project=jobs-serving-platform \
  --zone=asia-southeast1-b \
  -- -N -L 3000:127.0.0.1:3000
```

- `--` chuyển các flag cuối cho SSH.
- `-N` chỉ mở tunnel, không chạy remote shell.
- `-L` ánh xạ port 3000 của terminal hiện tại tới Dagster localhost trên VM.
- Mở <http://localhost:3000>. Nếu chạy từ Cloud Shell, dùng **Web Preview → port 3000**.

## 21. Bước 16 — Deploy staging bằng GitHub Actions

**Mục đích:** kiểm tra chất lượng, build image tag bằng commit SHA, push Artifact Registry, deploy
Cloud Run staging private và smoke bằng identity token.

Workflow chỉ tự chạy khi có push mới vào `GCP_Deploy`. Chạy trên clone có quyền push:

```bash
git switch GCP_Deploy
git status
git push origin HEAD:refs/heads/GCP_Deploy
```

- `git switch` bảo đảm đúng branch.
- `git status` giúp tránh push nhầm file secret/uncommitted.
- `git push HEAD:refs/heads/GCP_Deploy` đẩy commit hiện tại vào branch remote chính xác.

GitHub không nhận account password. Dùng `gh auth login` hoặc PAT khi Git hỏi password.

Nếu output là `Everything up-to-date`, không có push event mới. Có hai lựa chọn:

1. GitHub Actions → run cũ → **Re-run all jobs**; dùng cùng commit SHA/image tag.
2. Khi thực sự cần một run mới mà source không đổi:

```bash
git commit --allow-empty -m "ci: redeploy staging"
git push origin HEAD:refs/heads/GCP_Deploy
```

- `--allow-empty` tạo commit mới không đổi file, từ đó tạo push event mới.
- Chỉ dùng khi có chủ đích; re-run run cũ thường sạch hơn.

Workflow thành công phải có ba job/nhóm chính:

```text
quality → build & push image → deploy-staging → private smoke
```

Image cùng SHA không cần tạo lại chỉ vì bạn đã chạy GitHub Actions. Artifact Registry lưu layer theo
digest và tái sử dụng layer trùng; kiểm image trước khi build thủ công.

## 22. Bước 17 — Lấy URL và smoke staging

**Mục đích:** xác nhận Cloud Run revision thật sự đọc được BigQuery và bảo vệ `/v1/*` bằng API key.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving
source infra/gcp/config.sh
STAGING_URL="$(gcloud run services describe "$RUN_SERVICE_STAGING" \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --format='value(status.url)')"
printf 'STAGING_URL=%s\n' "$STAGING_URL"
```

- `gcloud run services describe` đọc object service đã deploy.
- `--format='value(status.url)'` hợp lệ vì đây là **gcloud**, không phải `bq`.
- Command substitution `$(...)` gán URL hiện hành của service vào biến; nó không tự tạo URL mới.

Nhập raw staging key mà không hiện trên màn hình:

```bash
read -r -s -p 'Dán raw staging API key: ' STAGING_API_KEY; echo
test -n "$STAGING_URL" || { echo "STAGING_URL đang rỗng"; exit 1; }
test -n "$STAGING_API_KEY" || { echo "STAGING_API_KEY đang rỗng"; exit 1; }
TOKEN="$(gcloud auth print-identity-token)"
BASE_URL="$STAGING_URL" \
API_KEY="$STAGING_API_KEY" \
AUTH_BEARER="$TOKEN" \
REQUIRE_API_KEY=1 \
  bash infra/gcp/smoke.sh
```

- `read -s` không echo secret.
- Hai lệnh `test -n` chặn lỗi header/URL rỗng trước khi gọi curl.
- `print-identity-token` tạo token cho tầng Cloud Run IAM khi service private.
- `AUTH_BEARER` vượt qua Cloud Run invoker; `API_KEY` vượt qua auth của ứng dụng. Hai lớp khác nhau.
- `REQUIRE_API_KEY=1` khiến smoke fail ngay nếu raw key trống.

Nếu đã chạy `bash infra/gcp/allow-public.sh staging`, tầng Cloud Run không cần bearer; `/v1/*` vẫn cần
API key.

## 23. Bước 18 — Deploy production thủ công

**Mục đích:** production không dùng WIF tự động. Owner kiểm SHA/image rồi deploy service đọc
`jobs_prod` và secret production.

### 23.1 Dùng image CI đã có

**`[Cloud Shell]`** tại repo root:

```bash
cd ~/jobs-api-serving
source infra/gcp/config.sh
SHA="$(git rev-parse HEAD)"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${AR_IMAGE}:${SHA}"
gcloud artifacts docker images describe "$IMAGE" --project="$PROJECT_ID"
IMAGE_TAG="$SHA" PUBLIC_ACCESS=1 bash infra/gcp/deploy-cloud-run.sh prod
```

- Workflow tag image bằng SHA **đầy đủ**, nên dùng `git rev-parse HEAD` thay vì SHA rút gọn.
- `artifacts docker images describe` xác nhận tag tồn tại trước deploy.
- `IMAGE_TAG=...` bảo script deploy đúng image.
- `PUBLIC_ACCESS=1` cho owner gán public invoker; ứng dụng vẫn yêu cầu production API key.

### 23.2 Chỉ build/push nếu image chưa có

```bash
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
docker build -t "$IMAGE" .
docker push "$IMAGE"
IMAGE_TAG="$SHA" PUBLIC_ACCESS=1 bash infra/gcp/deploy-cloud-run.sh prod
```

- `configure-docker` cấu hình Docker credential helper cho Artifact Registry hostname.
- `docker build ... .` phải chạy ở repo root vì dấu `.` là build context chứa Dockerfile/app.
- `docker push` upload layer và tag.
- Lệnh cuối deploy image vừa kiểm/build.

Nếu `docker push` gặp `connect: connection refused` nhưng `curl -I` trả 405, mạng đã tới registry;
405 là do HEAD. Chờ ngắn rồi chạy lại `docker push "$IMAGE"`. Không đổi tag hoặc build image khác
chỉ vì lỗi mạng tạm thời.

### 23.3 Smoke production

```bash
PROD_URL="$(gcloud run services describe "$RUN_SERVICE_PROD" \
  --project="$PROJECT_ID" --region="$REGION" \
  --format='value(status.url)')"
read -r -s -p 'Dán raw production API key: ' PROD_API_KEY; echo
test -n "$PROD_URL" || { echo "PROD_URL rỗng"; exit 1; }
test -n "$PROD_API_KEY" || { echo "PROD_API_KEY rỗng"; exit 1; }
BASE_URL="$PROD_URL" API_KEY="$PROD_API_KEY" REQUIRE_API_KEY=1 \
  bash infra/gcp/smoke.sh
```

Production public không cần identity token. Kỳ vọng 6 pass, 0 fail.

Nếu `/v1/market/metrics` trả 500 trong khi metadata/search 200:

1. xác minh đang deploy commit có query BigQuery quote cột `` `window` ``;
2. xác minh prod đã publish `gold_market_metrics`;
3. xem Cloud Run log bằng:

```bash
gcloud run services logs read "$RUN_SERVICE_PROD" \
  --project="$PROJECT_ID" --region="$REGION" --limit=100
```

Không bỏ smoke metrics khỏi pipeline; lỗi này nghĩa là serving contract chưa đạt.

Nếu Dagster/ELT trả lỗi BigQuery:

```text
Query without FROM clause cannot have a WHERE clause
```

đó là SQL bootstrap `warehouse_state` ở bản serving repo cũ, không phải lỗi IAM hay dữ liệu Mongo.
Cập nhật source trên VM và xác minh bản mới có `FROM (SELECT 1)`:

```bash
cd /opt/jobs-serving-api
git fetch origin
git switch GCP_Deploy
git pull --ff-only origin GCP_Deploy
grep -n 'FROM (SELECT 1)' app/elt/serving/publish.py
sudo systemctl restart dagster-daemon dagster-webserver
```

- Ba lệnh Git đưa VM tới source mới mà không tạo merge commit ngoài ý muốn.
- `grep` phải tìm thấy dòng bootstrap.
- Restart làm Dagster code server nạp source mới. Rerun job sẽ tạo batch mới; publish transaction lỗi
  trước đó không được coi là thành công.

## 24. Bước 19 — Tạo reporting core views

**Mục đích:** tạo lớp view read-only ổn định cho Looker Studio, tách dashboard khỏi schema warehouse
và chỉ đọc current production batch.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 85-reporting-views.sh
bq --project_id="$PROJECT_ID" ls --format=pretty \
  "$PROJECT_ID:$DATASET_REPORTING"
```

- `85` tạo `jobs_reporting`, các core view `rpt_*`, authorized-dataset access tới `jobs_prod` và chạy
  invariants.
- `bq ls --format=pretty` liệt kê view bằng format hợp lệ.

Nếu dataset `jobs_reporting` đã có từ trước, script không tin mù: nó kiểm location, replace view và
verify. Dataset không có column; từng view mới có schema.

## 25. Bước 20 — Tạo Logging sink và sinh traffic

**Mục đích:** chuyển log JSON mới của Cloud Run production vào BigQuery để dashboard có request,
latency, cache, bytes billed và 429.

### 25.1 Tạo sink

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 86-logging-sink.sh
```

Script tạo/cập nhật:

- dataset `jobs_prod_logs`, default partition expiration 90 ngày;
- sink `jobs-api-logs-to-bq` chỉ lọc event allowlist của `jobs-serving-api-prod`;
- writer identity có quyền ghi ở cấp dataset.

Sink **không hồi tố**. Ngay sau lệnh này, `bq ls jobs_prod_logs` chưa có gì là bình thường.

### 25.2 Sinh log mới hợp lệ

```bash
cd ~/jobs-api-serving
BASE_URL="$PROD_URL" API_KEY="$PROD_API_KEY" REQUIRE_API_KEY=1 \
  bash infra/gcp/smoke.sh
```

Lệnh gọi search/metrics và tạo các event nằm trong filter sink.

Để tạo sample rate-limit 429:

```bash
test -n "$PROD_URL" || { echo "PROD_URL rỗng"; exit 1; }
test -n "$PROD_API_KEY" || { echo "PROD_API_KEY rỗng"; exit 1; }
seq 1 200 | xargs -P 20 -I{} \
  curl -sS -o /dev/null -w '%{http_code}\n' \
    -H "X-API-Key: ${PROD_API_KEY}" \
    "${PROD_URL}/v1/metadata" | sort | uniq -c
```

- `seq 1 200` sinh 200 input item.
- `xargs -P 20` chạy tối đa 20 curl song song.
- `-o /dev/null` bỏ body; `-w` chỉ in status.
- `sort | uniq -c` đếm mỗi status. Có thể thấy 200 và 429.
- Nếu chỉ thấy `000`, dừng lại: đó là lỗi kết nối/biến/lệnh paste, không phải rate limit.

Chờ vài phút rồi kiểm:

```bash
bq --project_id="$PROJECT_ID" ls --format=pretty \
  "$PROJECT_ID:$DATASET_LOGS"
```

Kỳ vọng có `run_googleapis_com_stdout`. Nếu chưa có, xác minh `RUN_SERVICE_PROD`, sink filter và log
prod mới bằng Cloud Logging Explorer.

## 26. Bước 21 — Tạo API reporting views

**Mục đích:** chuẩn hóa bảng log bán cấu trúc thành view Looker dễ dùng.

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 87-api-reporting-views.sh
```

Script yêu cầu table log đã tồn tại và partition expiration đúng. Nó tạo:

- `rpt_api_requests`;
- `rpt_api_latency_hourly`;
- `rpt_api_cache_events`;
- `rpt_api_bq_queries_daily`;
- `rpt_api_429_by_client`.

Source đúng hiện tại dùng `DIV(CAST(status AS INT64), 100)` và quote `` `window` ``/`` `groups` ``.
Nếu vẫn gặp các syntax error cũ, clone trên Cloud Shell chưa cập nhật branch `GCP_Deploy`.

## 27. Bước 22 — Cấp quyền và tạo Looker Studio dashboard

**Mục đích:** cho Looker Studio chạy query trên `jobs_reporting` bằng Owner's Credentials, không cấp
quyền trực tiếp vào `jobs_prod` hay `jobs_prod_logs` cho viewer.

### 27.1 Cấp quyền cho owner data source

**`[GCP Console]`**:

1. IAM → Grant Access → principal là Google account sẽ sở hữu data source.
2. Cấp **BigQuery Job User** ở project `jobs-serving-platform`.
3. BigQuery → dataset `jobs_reporting` → **Sharing → Permissions → Add principal**.
4. Cấp **BigQuery Data Viewer** chỉ trên dataset này.

Script 85/87 đã cấu hình `jobs_reporting` là authorized reader của hai dataset nguồn. Không cần cấp
owner quyền trực tiếp vào raw production/log datasets.

### 27.2 Kết nối data source

**`[Looker Studio]`**:

1. Mở <https://lookerstudio.google.com/> → **Create → Report**.
2. Chọn connector **BigQuery**.
3. Chọn project `jobs-serving-platform` → dataset `jobs_reporting` → một view `rpt_*`.
4. Chọn **Owner's Credentials**.
5. Lặp lại cho các view cần dùng.

Freshness đề xuất:

- market/core views: 12 giờ;
- pipeline/API views: 5 phút;
- không dùng Extract Data cho trang vận hành vì extract là snapshot tĩnh.

### 27.3 Sáu trang dashboard

Chi tiết field/chart nằm tại `docs/looker-studio/dashboard-plan.md`. Bố cục tóm tắt:

1. **Tổng quan:** KPI, cơ cấu nguồn/cấp bậc, batch trend.
2. **Thị trường:** category, source, seniority, company, posting trend.
3. **Lương:** median theo category/seniority/source; filter ``window``; k-anonymity.
4. **Pipeline ELT:** current batch, silver/quarantine/gold, lineage.
5. **Chất lượng:** quarantine theo reason/stage/source/batch.
6. **API serving:** request, p50/p95, status, cache, BQ bytes, 429/client.

## 28. Bước 23 — Tuần 8: OpenTelemetry và Cloud Trace

**Mục đích:** tạo distributed trace cho request FastAPI, span `bq.query`, và liên kết trace với
structured log.

Source hiện tại dùng OTLP/gRPC tới `telemetry.googleapis.com`; không dùng exporter Cloud Trace cũ.
Runtime SA cần `roles/telemetry.tracesWriter` và `roles/serviceusage.serviceUsageConsumer`.

### 28.1 Đồng bộ API/IAM Tuần 8

**`[Cloud Shell]`**

```bash
cd ~/jobs-api-serving/infra/gcp
source config.sh
bash 01-enable-apis.sh
bash 21-iam-bindings.sh
```

- Chạy lại `01` bảo đảm Telemetry API và Cloud Trace API enabled.
- Chạy lại `21` gán hai role tracing cho staging/prod runtime SA.
- Cả hai script idempotent.

### 28.2 Redeploy image có OpenTelemetry

Push/re-run staging và deploy production theo Bước 16–18. `deploy-cloud-run.sh` tự đặt:

```text
JOBS_API_OTEL_TRACES_EXPORTER=otlp
JOBS_API_OTEL_SAMPLING_RATIO=0.1
```

Không cần sampling 1.0 để nghiệm thu vì smoke có thể gửi W3C `traceparent` với sampled flag `01`.

### 28.3 Chủ động tạo một trace

**Production public:**

```bash
cd ~/jobs-api-serving
TRACE_DEMO=1 REQUIRE_API_KEY=1 \
BASE_URL="$PROD_URL" API_KEY="$PROD_API_KEY" \
  bash infra/gcp/smoke.sh
```

**Staging private:**

```bash
TOKEN="$(gcloud auth print-identity-token)"
TRACE_DEMO=1 REQUIRE_API_KEY=1 \
BASE_URL="$STAGING_URL" API_KEY="$STAGING_API_KEY" AUTH_BEARER="$TOKEN" \
  bash infra/gcp/smoke.sh
```

- `TRACE_DEMO=1` sinh trace ID 32 ký tự và gắn `traceparent` sampled vào request search.
- `REQUIRE_API_KEY=1` ngăn nghiệm thu giả khi key rỗng.
- Copy `trace_id` mà script in ra.

### 28.4 Xem trace

**`[GCP Console]`** mở **Trace Explorer**, chọn project `jobs-serving-platform`, lọc thời gian gần
nhất và tìm trace ID. Trace mong đợi có server span FastAPI và child span `bq.query`.

Kiểm bằng REST trong Cloud Shell:

```bash
read -r -p 'Dán trace_id 32 ký tự: ' TRACE_ID
ACCESS_TOKEN="$(gcloud auth print-access-token)"
curl -sS \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "x-goog-user-project: ${PROJECT_ID}" \
  "https://cloudtrace.googleapis.com/v1/projects/${PROJECT_ID}/traces/${TRACE_ID}" \
  | python3 -m json.tool
```

Giải thích:

- `read` nhận trace ID, không đặt URL/JSON vào prompt.
- `print-access-token` lấy OAuth access token của user Cloud Shell.
- `Authorization` xác thực user.
- `x-goog-user-project` chỉ định project chịu quota; đây là phần thiếu trong lỗi 403 trước đây.
- `python3 -m json.tool` format response JSON.

Nếu một chương trình local dùng Application Default Credentials thay vì token gcloud:

```bash
gcloud auth application-default login
gcloud auth application-default set-quota-project "$PROJECT_ID"
```

- Lệnh đầu tạo local ADC cho client libraries.
- Lệnh hai ghi quota project vào ADC. Không cần hai lệnh này cho Cloud Run vì runtime dùng VM/service
  identity và deploy đã cấp Service Usage Consumer.

## 29. Checklist nghiệm thu cuối cùng

- [ ] Project/billing đúng và budget alert đã tạo.
- [ ] `jobs_staging`, `jobs_prod`, `jobs_reporting`, `jobs_prod_logs` cùng location.
- [ ] Năm SA chính và restore SA tồn tại; không có user-managed JSON key do runbook tạo.
- [ ] API-key secret staging/prod có version enabled; page-token secret có version.
- [ ] VM gắn `sa-dagster-elt`, Mongo/Dagster/timer active.
- [ ] Dry-run ELT đạt; staging và prod mỗi bên có published batch.
- [ ] Backup object tồn tại; restore test vào DB tạm đạt.
- [ ] GitHub Actions quality/build/deploy/smoke xanh.
- [ ] Cloud Run staging/prod smoke đủ 6 check.
- [ ] Logging sink tạo table partitioned và API reporting views query được.
- [ ] Looker Studio đọc `jobs_reporting` bằng Owner's Credentials.
- [ ] Trace demo xuất hiện trong Cloud Trace và có span BigQuery.

## 30. Xử lý sự cố nhanh

### `git: command not found`

Chạy trên VM:

```bash
sudo apt-get update
sudo apt-get install -y git
```

### Clone `/opt/...`: permission denied

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/jobs-serving-api
```

Directory phải rỗng hoặc là Git repo đúng. Không `sudo git clone` vì file sau đó sẽ thuộc root.

### GitLab/GitHub HTTP Basic denied

- GitLab private: PAT có `read_repository` ở prompt password.
- GitHub push: PAT có quyền repo hoặc `gh auth login`; account password không còn được hỗ trợ.

### `python: command not found`

```bash
/opt/jobs-serving-api/.venv/bin/python -m app.elt.serving.run_serving_elt --help
```

Nếu file không tồn tại, tạo venv/cài requirements theo Bước 10.

### Dagster active nhưng log Alembic migration

Chạy lại mục 17.1, rồi đọc `journalctl`. Nếu lỗi nói `more than one head`, dùng nhánh tạo
`DAGSTER_HOME` sạch đã mô tả ở đó. Không chỉ dựa vào dòng `Active: active`.

### ELT báo `Query without FROM clause cannot have a WHERE clause`

Đây là source serving cũ có bootstrap SQL sai. Cập nhật `/opt/jobs-serving-api` từ `GCP_Deploy`, xác
minh `publish.py` có `FROM (SELECT 1)`, restart Dagster rồi rerun.

### Backup in “upload” rồi object không tồn tại

Upload chưa hoàn tất. Cần thấy `Backup xong`. Nếu 403 `storage.objects.get`, chạy lại
`80-backup-gcs.sh` từ Cloud Shell.

### Smoke trả 401 “Thiếu header X-API-Key”

```bash
test -n "${STAGING_API_KEY:-}" && echo "key variable có giá trị" || echo "key variable RỖNG"
```

Nhập lại raw key. Không dùng JSON/hash làm header.

### Load test trả `000`

```bash
printf 'URL length=%s; key length=%s\n' "${#PROD_URL}" "${#PROD_API_KEY}"
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-API-Key: ${PROD_API_KEY}" "${PROD_URL}/v1/metadata"
```

Chỉ in độ dài, không in secret. Sửa single request trước khi chạy 200 request.

### `jobs_prod_logs` rỗng

Sink chỉ nhận log mới. Chạy smoke production sau khi tạo sink, chờ vài phút, rồi xem sink/filter và
Cloud Logging.

### Trace REST quota-project 403

Thêm `x-goog-user-project` như Bước 28.4. Nếu vẫn 403, kiểm user có quyền đọc Cloud Trace và API đã
enabled.

## 31. Phụ lục — Sổ tay lệnh Cloud Shell dùng trong project

Phần này giải thích cú pháp chung để người đọc hiểu lệnh, không chỉ copy/paste.

### 31.1 Điều hướng và file

| Lệnh | Ý nghĩa |
| --- | --- |
| `pwd` | In directory hiện tại |
| `ls -la` | Liệt kê cả file ẩn, quyền, owner và size |
| `cd PATH` | Chuyển directory |
| `cd ~` | Về home user |
| `cp SOURCE DEST` | Sao chép file |
| `mv SOURCE DEST` | Di chuyển/đổi tên; runbook dùng để giữ backup directory lỗi |
| `nano FILE` | Sửa file bằng terminal editor |
| `test -f FILE` | Thành công nếu file tồn tại |
| `test -n "$VAR"` | Thành công nếu biến không rỗng |
| `chmod 600 FILE` | Chỉ owner được đọc/ghi file |

### 31.2 Biến shell và pipe

| Cú pháp | Ý nghĩa |
| --- | --- |
| `NAME="value"` | Tạo biến shell, chưa tự truyền cho process con |
| `export NAME="value"` | Tạo và export biến cho process con |
| `source file.sh` | Chạy file trong shell hiện tại; các export được giữ lại |
| `NAME="value" command` | Chỉ truyền biến cho đúng command đó |
| `$(command)` | Lấy stdout của command làm giá trị |
| `command1 \| command2` | Đưa stdout của command1 vào stdin command2 |
| `unset NAME` | Xóa biến khỏi phiên shell |
| `read -r -p 'Prompt: ' NAME` | Đọc input vào biến, giữ nguyên backslash |
| `read -r -s ...` | Đọc secret nhưng không echo ký tự |
| `set -a; source .env; set +a` | Export toàn bộ biến từ file env cho process con |
| `command || true` | Không để exit code khác 0 làm dừng chuỗi; chỉ dùng khi lỗi đó thực sự được phép bỏ qua |

Backslash `\` ở cuối dòng Bash nghĩa là command còn tiếp ở dòng sau. Không thêm ký tự hoặc khoảng
trắng sau backslash. Đừng copy các ký tự escape do ứng dụng chat hiển thị như `\--project` hay
`\_`; câu lệnh thật phải là `--project` và `_` bình thường.

### 31.3 Git

| Lệnh | Ý nghĩa |
| --- | --- |
| `git clone --branch X --single-branch URL DIR` | Clone đúng branch X vào DIR |
| `git -C DIR ...` | Chạy Git trong DIR |
| `git branch --show-current` | In branch hiện tại |
| `git rev-parse HEAD` | In commit SHA đầy đủ |
| `git fetch origin` | Tải trạng thái remote, chưa merge |
| `git switch GCP_Deploy` | Chuyển branch |
| `git pull --ff-only` | Cập nhật mà không tạo merge commit |
| `git status` | Xem file modified/untracked và branch |
| `git push origin HEAD:refs/heads/GCP_Deploy` | Push commit hiện tại vào remote branch cụ thể |
| `git commit --allow-empty ...` | Tạo commit không đổi file để chủ động phát sinh push event |

### 31.4 gcloud: cấu hình và xác thực

| Lệnh | Ý nghĩa |
| --- | --- |
| `gcloud config set project ID` | Đặt project mặc định |
| `gcloud config get-value project` | Xem project mặc định |
| `gcloud auth list` | Xem account đã đăng nhập |
| `gcloud auth print-access-token` | Lấy OAuth token gọi Google REST APIs |
| `gcloud auth print-identity-token` | Lấy OIDC identity token gọi Cloud Run private |
| `gcloud auth application-default login` | Tạo ADC local cho client libraries |
| `gcloud auth application-default set-quota-project ID` | Gắn quota project vào ADC user |

Access token và identity token không thay thế nhau: access token gọi Google APIs; identity token
chứng minh danh tính với một service như Cloud Run.

### 31.5 gcloud: project, API, IAM và secrets

| Lệnh | Ý nghĩa |
| --- | --- |
| `gcloud projects describe ID` | Đọc metadata/project number |
| `gcloud services enable API...` | Bật API; idempotent |
| `gcloud iam service-accounts list/describe` | Liệt kê/đọc SA |
| `gcloud projects add-iam-policy-binding` | Gán role cấp project |
| `gcloud iam service-accounts add-iam-policy-binding` | Gán quyền trên một SA, ví dụ actAs/impersonate |
| `gcloud secrets versions add NAME --data-file=-` | Tạo secret version từ stdin |
| `gcloud secrets versions list NAME` | Xem version/state, không đọc payload |

`--member` là danh tính nhận quyền; `--role` là tập permission; resource đứng sau command là nơi
quyền được gán.

### 31.6 Compute Engine

| Lệnh | Ý nghĩa |
| --- | --- |
| `gcloud compute instances create` | Tạo VM |
| `gcloud compute instances describe` | Đọc cấu hình/trạng thái VM |
| `gcloud compute ssh` | Quản lý SSH key và kết nối VM |
| `gcloud compute instances stop NAME` | Dừng VM để ngừng compute charge khi không dùng |
| `gcloud compute instances start NAME` | Khởi động lại VM |

Luôn truyền `--project` và `--zone` hoặc đặt biến đúng trước khi thao tác VM.

### 31.7 BigQuery CLI (`bq`)

| Lệnh | Ý nghĩa |
| --- | --- |
| `bq ls --datasets` | Liệt kê dataset |
| `bq ls PROJECT:DATASET` | Liệt kê table/view trong dataset |
| `bq show --dataset PROJECT:DATASET` | Xem metadata dataset |
| `bq show --schema PROJECT:DATASET.TABLE` | Xem column schema table/view |
| `bq mk --dataset --location=...` | Tạo dataset |
| `bq query --use_legacy_sql=false 'SQL'` | Chạy GoogleSQL/Standard SQL |
| `--parameter='name:TYPE:value'` | Truyền named query parameter an toàn |
| `--format=prettyjson` | Output JSON dễ đọc |

Không dùng `--format='value(...)'` với `bq`; đó là projection syntax của `gcloud`.

### 31.8 Artifact Registry, Cloud Run và GCS

| Lệnh | Ý nghĩa |
| --- | --- |
| `gcloud auth configure-docker HOST` | Cho Docker dùng gcloud credential helper |
| `gcloud artifacts docker images describe IMAGE` | Kiểm image/tag có tồn tại |
| `docker build -t IMAGE .` | Build Docker image từ repo root |
| `docker push IMAGE` | Push image/layer vào Artifact Registry |
| `gcloud run services describe SERVICE` | Đọc URL/config Cloud Run service |
| `gcloud run services logs read SERVICE` | Đọc log service |
| `gcloud storage ls -l gs://...` | Liệt kê object với size/time |
| `gcloud storage cp SOURCE DEST` | Upload/download object |

### 31.9 HTTP và xử lý output

| Lệnh/cờ | Ý nghĩa |
| --- | --- |
| `curl -sS` | Ẩn progress nhưng vẫn hiện lỗi |
| `curl -i` | In response headers và body |
| `curl -I` | Gửi HEAD; không tương đương GET |
| `-H 'Name: value'` | Thêm request header |
| `-X POST` | Chọn HTTP method POST |
| `-d 'JSON'` | Gửi request body |
| `-o /dev/null` | Bỏ body |
| `-w '%{http_code}'` | In status code |
| `python3 -m json.tool` | Format/validate JSON từ stdin |
| `sort \| uniq -c` | Gom và đếm các dòng giống nhau |
| `seq ... \| xargs -P N` | Chạy nhiều request song song có giới hạn |

HTTP `000` do curl tự in khi không nhận được HTTP response; nó không phải status do API trả về.

### 31.10 Lệnh vận hành trên VM

Các lệnh này không phải API GCP nhưng xuất hiện thường xuyên trong project:

| Lệnh | Ý nghĩa |
| --- | --- |
| `sudo systemctl status UNIT` | Xem trạng thái service |
| `sudo systemctl restart UNIT` | Restart và nạp lại EnvironmentFile |
| `sudo systemctl list-timers` | Xem lịch systemd timer |
| `sudo journalctl -u UNIT -n 200` | Xem log gần nhất của unit |
| `docker compose ... up -d` | Tạo/start container nền |
| `docker compose ... ps` | Xem container trạng thái/cổng |
| `docker exec CONTAINER COMMAND` | Chạy command trong container |
| `pnpm install --frozen-lockfile` | Cài đúng dependency theo lockfile |
| `pnpm start -- --platform ...` | Chạy crawler và chuyển flag cho app |
| `.venv/bin/python -m MODULE` | Chạy đúng Python/module của project |

## 32. Tài liệu tham chiếu

- `infra/gcp/README.md`: script foundation.
- `migrate-report/phase-2/README.md`: ELT và kiểm chứng BigQuery.
- `migrate-report/phase-6/README.md`: Cloud Run/CI/CD.
- `migrate-report/phase-7/README.md`: VM và backup/restore.
- `docs/looker-studio/dashboard-plan.md`: cấu hình từng trang/dashboard.
- `docs/adr/ADR-027-opentelemetry-otlp-cloud-trace.md`: quyết định tracing.
- [Google Cloud: tạo VM với user-managed service account](https://cloud.google.com/compute/docs/access/create-enable-service-accounts-for-instances).
- [Google Cloud: migrate Cloud Trace exporter sang OTLP](https://cloud.google.com/trace/docs/migrate-to-otlp-endpoints).
- [Google Cloud: đặt quota project](https://cloud.google.com/docs/quotas/set-quota-project).
- [Looker Studio: kết nối BigQuery](https://cloud.google.com/looker/docs/studio/connect-to-google-bigquery).
