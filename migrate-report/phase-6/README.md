# Migration Report — Phase 6: Cloud Run + CI/CD + staging/prod

## 1. Tổng quan

Phase 6 đưa **Jobs Serving API lên Cloud Run** với CI/CD tách môi trường:

```text
push nhánh GCP_Deploy
        │
        ▼
GitHub Actions ── WIF/OIDC ──► sa-ci-deployer-staging
        │ (quality xanh mới deploy)
        ├─ build image tag = commit SHA ──► Artifact Registry
        ├─ deploy Cloud Run staging (private, chạy dưới sa-api-reader-staging)
        └─ smoke CỔNG CỨNG qua identity token (gọi được cả khi private)

prod: build+push+deploy THỦ CÔNG bằng creds owner (không WIF) — script lặp lại được
```

Mục tiêu: mỗi commit trên `GCP_Deploy` cho ra một revision staging **truy vết được theo SHA**, đã qua
lint+test và smoke; prod deploy có kiểm soát (thủ công), tách danh tính/dataset/secret khỏi staging.

### Trạng thái

| Phạm vi | Trạng thái | Ý nghĩa |
| --- | --- | --- |
| Script deploy + CI/CD trong repo | **Hoàn thành** | `deploy-cloud-run.sh`, `allow-public.sh`, `smoke.sh`, `.github/workflows/ci.yml` |
| Quyết định kiến trúc | **Đã chấp nhận** | `docs/adr/ADR-022` §"Phase 6 — quyết định triển khai" |
| Deploy trên GCP thật | **Cần thực hiện theo project** | Chạy sau khi infra `20→70` xong + nạp secret (mục 4–5) |
| Prod rehearsal | **Chưa ghi nhận** | Điền mục 7 sau lần deploy prod đầu tiên |

---

## 2. Các nhánh lựa chọn đã chốt (xem ADR-022)

- **(1A)** Cấu hình = script `gcloud run deploy` + flag (không YAML/Terraform).
- **(2A)** CI deploy staging tự động khi push `GCP_Deploy`, gate sau job `quality`; prod thủ công.
- **(3A)** memory-first: staging cache & rate-limit in-process; Redis + Direct VPC egress chỉ bật qua
  `WITH_REDIS=1` (sau khi chạy `60-networking-redis.sh`).
- **(B)** CI deployer giữ `run.developer`; public = `allow-public.sh` chạy 1 lần bằng owner.
- **Smoke = cổng cứng**, gọi service private bằng identity token (deployer SA cần `run.invoker`).

---

## 3. Deliverable (file trong repo)

| File | Vai trò |
| --- | --- |
| `infra/gcp/deploy-cloud-run.sh` | Deploy `staging\|prod`: image=SHA, SA reader theo env, secret pin version, cost guard, toggle `WITH_REDIS`/`PUBLIC_ACCESS` |
| `infra/gcp/allow-public.sh` | Bind `allUsers→run.invoker` một lần (owner) để mở public |
| `infra/gcp/smoke.sh` | Smoke `/health`, `/v1/metadata`, `/v1/jobs/search` (POST), `/v1/market/metrics` ×2; hỗ trợ `AUTH_BEARER` |
| `infra/gcp/21-iam-bindings.sh` | (+) `run.invoker` cấp project cho staging deployer (CI smoke) |
| `infra/gcp/config.example.sh` | (+) section Cloud Run runtime (service name, cpu/mem/concurrency/timeout) |
| `.github/workflows/ci.yml` | Job `deploy-staging`: WIF → build/push SHA → deploy → mint id token → smoke |

---

## 4. Chuẩn bị (làm 1 lần)

1. **Infra Phase 1 `20→70` đã chạy.** Vì Phase 6 thêm `run.invoker`, **chạy lại**:
   ```bash
   cd infra/gcp && bash 21-iam-bindings.sh
   ```
2. **Đặt secret trong GitHub** (repo → Settings → Secrets and variables → Actions). Lấy 2 giá trị WIF
   từ output `50-wif.sh`:
   | Secret | Giá trị |
   | --- | --- |
   | `GCP_PROJECT_ID` | Project ID (vd `jobs-serving-471203`) |
   | `GCP_WIF_PROVIDER` | `projects/<num>/locations/global/workloadIdentityPools/github-pool/providers/github-provider` |
   | `GCP_DEPLOYER_SA` | `sa-ci-deployer-staging@<project>.iam.gserviceaccount.com` |
   | `SMOKE_API_KEY` *(tuỳ chọn)* | key THÔ để smoke `/v1` (mục 5); thiếu thì smoke chỉ chạy `/health` + kiểm 401 |
3. **Kiểm** `config.example.sh` có `GITHUB_REPO="owner/repo"` đúng repo GitHub của bạn (WIF khoá theo repo).

---

## 5. Nạp secret api-keys (trước deploy môi trường tương ứng)

Secret `jobs-api-keys-{staging,prod}` do `40-secrets.sh` tạo đang RỖNG. Phát key và nạp:

```bash
# 1) sinh client_id + key thô + hash (key thô chỉ hiện MỘT LẦN)
python -m scripts.issue_key team-ai            # hoặc --days 90

# 2) nạp JSON đã hash thành version mới của secret (staging)
printf '%s' '{"team-ai":{"key_sha256":"<hash-từ-bước-1>","expires_at":null}}' \
  | gcloud secrets versions add jobs-api-keys-staging --data-file=- --project <PROJECT_ID>
```

- Đưa **key thô** cho consumer; để smoke `/v1` chạy trong CI, dán key thô vào GitHub secret
  `SMOKE_API_KEY`. Nạp prod tương tự với `jobs-api-keys-prod`.
- `deploy-cloud-run.sh` tự **pin version ENABLED mới nhất** lúc deploy (không cần chỉ định tay).

---

## 6. Deploy

### 6.1 Staging (tự động)

```bash
git push origin GCP_Deploy
```
CI chạy: `quality` → build/push image `:$SHA` → deploy staging (private) → smoke (cổng cứng).
**Lần đầu**, mở public cho người dùng thật (owner, 1 lần — smoke KHÔNG cần bước này):
```bash
cd infra/gcp && bash allow-public.sh staging
```

### 6.2 Prod (thủ công, lặp lại được)

Chạy bằng **creds owner** (Cloud Shell). Prod không qua CI/WIF; `PUBLIC_ACCESS=1` mặc định nên owner
set public luôn trong lúc deploy.

```bash
# tại THƯ MỤC GỐC repo
source infra/gcp/config.sh
SHA="$(git rev-parse --short=12 HEAD)"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${AR_IMAGE}:${SHA}"
docker build -t "${IMAGE}" .          # Dockerfile ở gốc repo
docker push "${IMAGE}"

# (nạp jobs-api-keys-prod nếu chưa — xem mục 5)
IMAGE_TAG="${SHA}" bash infra/gcp/deploy-cloud-run.sh prod

# smoke tay (prod đã public → không cần AUTH_BEARER)
BASE_URL="<url-prod>" API_KEY="<key-thô-prod>" bash infra/gcp/smoke.sh
```

### 6.3 Bật Redis + Direct VPC egress (khi cần demo, tốn phí)

```bash
CONFIRM_REDIS=1 bash infra/gcp/60-networking-redis.sh      # tạo Memorystore, in ra IP private
WITH_REDIS=1 REDIS_HOST=<ip> IMAGE_TAG="$SHA" bash infra/gcp/deploy-cloud-run.sh staging
bash infra/gcp/99-teardown.sh redis                        # xoá khi hết demo
```

---

## 7. Ghi nhận prod rehearsal (điền sau lần đầu)

| Mục | Giá trị |
| --- | --- |
| Ngày deploy | _(điền)_ |
| Commit SHA | _(điền)_ |
| Service URL prod | _(điền)_ |
| Smoke | _(pass/fail; dán tóm tắt)_ |
| Ghi chú | _(sự cố/điều chỉnh, nếu có)_ |

---

## 8. Cost guard & an toàn (nhắc lại)

- `--max-instances` + `--cpu/--memory/--concurrency/--timeout` chặn scale ngoài ý muốn.
- `bq_maximum_bytes_billed` trần byte mỗi query (đặt qua env deploy).
- Redis mặc định **không tạo**; nhớ `99-teardown.sh redis` sau demo.
- Không JSON key (WIF); secret ngoài git; staging không đọc được dataset/secret prod (IAM cấp dataset).
- Image tag = SHA + cleanup policy AR (giữ 10 tag gần nhất) → không phình dung lượng.
