# Migration Report — Phase 7: VM ops + backup/restore

## 1. Tổng quan

Phase 7 dựng **VM vận hành** cho nguồn dữ liệu + orchestration, và **backup/restore** MongoDB:

```text
VM (asia-southeast1) — gắn sa-dagster-elt (trusted boundary)
  ├─ Mongo (container, docker-compose.mongo.yml, bind 127.0.0.1:27017)
  ├─ scraper (pnpm) — crawl TopDev + VietnamWorks
  ├─ Dagster: systemd dagster-webserver (UI 127.0.0.1:3000) + dagster-daemon (schedule/queue)
  │     └─ daily_serving_batch 02:00 → crawl → run_serving_elt (ghi BigQuery)
  └─ backup daily 03:30 (systemd timer): mongodump → GCS (objectCreator, immutable)
                                              │
                          restore identity riêng (sa-backup-restore, objectViewer)
                                              ▼
                                  restore.sh → DB tạm (không đè prod)
```

Hai repo: **`job-scraper-1`** (`deploy/`, compose, mongo-init) + **serving-api** (`infra/gcp/80-backup-gcs.sh`).

### Trạng thái

| Phạm vi | Trạng thái | Ý nghĩa |
| --- | --- | --- |
| Script provisioning + systemd + backup/restore | **Hoàn thành (code)** | Trong 2 repo, chưa commit tại thời điểm viết |
| Quyết định kiến trúc | **Đã chấp nhận** | ADR-022 §"Phase 7 — VM ops + backup" |
| VM thật + backup daily | **Cần thực hiện theo VM** | Chạy provisioning + 80-backup-gcs (mục 4) |
| **Restore test lần đầu** | **CHƯA làm — NGAY** | Bắt buộc Phase 7 (mục 6); điền bảng mục 7 sau khi chạy |

---

## 2. Nhánh lựa chọn đã chốt

- **(1A)** Mongo VM = container (compose mongo-only, bind localhost, fail-fast password).
- **(2B)** Dagster = 2 systemd unit (webserver + daemon), QueuedRunCoordinator + pool `serving_pipeline=1`.
- **(B)** Backup writer = `sa-dagster-elt` (`objectCreator`, tạo-chỉ → bất biến, không impersonation);
  restore identity riêng `sa-backup-restore` (`objectViewer`); bucket **lifecycle** (không retention-lock).

---

## 3. Deliverable (file)

**Repo `job-scraper-1`:**

| File | Vai trò |
| --- | --- |
| `docker-compose.mongo.yml` | Mongo VM (chỉ mongo, localhost, fail-fast password) |
| `mongo-init.js` | Bỏ hard-code; reader user secret-hoá qua env |
| `docker-compose.yml` | (dev) bỏ default mật khẩu yếu |
| `.gitignore` / `.env.example` | Unignore compose + `!.env.example`; mẫu env đầy đủ |
| `orchestration/workspace.yaml` · `dagster.yaml` | Code location + instance (coordinator + concurrency) |
| `deploy/systemd/dagster-webserver.service` · `dagster-daemon.service` | 2 unit Dagster |
| `deploy/systemd/mongo-backup.service` · `.timer` | Backup daily 03:30 |
| `deploy/provision-vm.sh` | Provisioning idempotent (docker/node/pnpm/gcloud/venv/systemd/timer/ufw) |
| `deploy/backup.sh` · `restore.sh` | mongodump→GCS · restore vào DB tạm |

**serving-api `infra/gcp/`:** `80-backup-gcs.sh` (bucket lifecycle + writer objectCreator + restore SA) · `config.example.sh` (biến backup).

---

## 4. Runbook — provisioning VM + backup

Thứ tự (chạy trên VM Ubuntu/Debian, đã clone cả 2 repo):

```bash
# --- 4.1 Hạ tầng backup GCS (một lần, có thể chạy ở Cloud Shell) ---
cd <serving-api>/infra/gcp && bash 80-backup-gcs.sh     # cần 20/21 đã tạo sa-dagster-elt

# --- 4.2 Cấu hình scraper ---
cd <job-scraper-1>
cp .env.example .env
nano .env    # điền MONGO_ROOT_PASSWORD (mạnh), MONGO_URI, JOBS_BQ_PROJECT, BACKUP_BUCKET (hoặc để trống)

# --- 4.3 Provisioning (docker+mongo, dagster systemd, backup timer, gcloud) ---
bash deploy/provision-vm.sh              # thêm CONFIRM_UFW=1 nếu muốn bật firewall
# → newgrp docker / logout-login nếu vừa cài docker lần đầu

# --- 4.4 Kiểm ---
systemctl status dagster-daemon
systemctl list-timers mongo-backup
docker compose -f docker-compose.mongo.yml ps
```

VM tự động: crawl+ELT 02:00 (Dagster), backup 03:30 (timer). Xem UI qua SSH tunnel:
```bash
ssh -L 3000:127.0.0.1:3000 <user>@<vm-ip>    # rồi mở http://localhost:3000
```

---

## 5. Kiểm backup thủ công (không chờ timer)

```bash
cd <job-scraper-1> && bash deploy/backup.sh
gcloud storage ls "gs://<bucket>/mongo/"      # thấy <db>-<UTC>.archive.gz
```

---

## 6. Restore test lần đầu (BẮT BUỘC — anh chạy)

Mục tiêu: chứng minh backup **khôi phục được** (backup không kiểm = không có backup). Dùng identity
restore riêng, phục hồi vào **DB tạm** để không đụng prod.

```bash
cd <job-scraper-1>
# Dùng SA restore (khuyến nghị) — impersonate cần quyền tokenCreator trên sa-backup-restore, hoặc
# chạy bằng creds owner (owner đọc được bucket).
IMPERSONATE_SA=sa-backup-restore@<PROJECT_ID>.iam.gserviceaccount.com bash deploy/restore.sh

# Đối chiếu số document giữa DB gốc và DB tạm:
docker exec job-crawler-mongo mongosh -u <root> -p '***' --authenticationDatabase admin --quiet \
  --eval 'print("prod:", db.getSiblingDB("job_crawler").jobs.countDocuments(),
                 "restore:", db.getSiblingDB("job_crawler_restore_test").jobs.countDocuments())'
```

**Đạt** khi: restore không lỗi + số document DB tạm khớp (xấp xỉ) DB gốc tại thời điểm backup. Dọn DB
tạm sau khi xong nếu muốn: `db.getSiblingDB("job_crawler_restore_test").dropDatabase()`.

---

## 7. Ghi nhận restore rehearsal (điền sau khi chạy)

| Mục | Giá trị |
| --- | --- |
| Ngày | _(điền)_ |
| Object backup dùng | _(gs://…)_ |
| Identity restore | _(sa-backup-restore / owner)_ |
| Document prod vs restore | _(vd 15289 vs 15289)_ |
| Kết quả | _(đạt/không + ghi chú)_ |

---

## 8. An toàn & chi phí (nhắc lại)

- Mongo/Dagster UI bind localhost; `ufw` opt-in; không mở 27017/3000 ra internet.
- Không mật khẩu trong file commit (compose fail-fast, mongo-init env); `.env` ngoài git.
- Backup writer chỉ `objectCreator` → **không xoá/ghi đè** (bất biến); restore identity tách riêng.
- Bucket lifecycle xoá backup cũ (cost guard); mã hoá at-rest mặc định.
- [SAU]: retention-lock/CMEK, disk snapshot định kỳ, restore drill định kỳ.
