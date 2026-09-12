# Hạ tầng GCP — Phase 1 (nền tảng)

Script `gcloud`/`bq` **idempotent** (chạy lại nhiều lần vẫn an toàn) để dựng nền tảng GCP cho
Jobs Serving API: BigQuery datasets, service accounts, Artifact Registry, secrets, WIF, Redis.
Bạn chạy các script này; chúng KHÔNG tự chạy. Quyết định kiến trúc: [ADR-022](../../docs/adr/).

> **Mới dùng GCP?** Làm theo đúng thứ tự dưới. Không cần cài gì trên máy — dùng **Cloud Shell**.

---

## 0. Chuẩn bị trên web (làm 1 lần)

1. Vào <https://console.cloud.google.com>.
2. **Tạo/chọn 1 Project.** Menu chọn project (góc trên) → *New Project* (hoặc dùng project sẵn có).
   Ghi lại **Project ID** (khác *Name* — là chuỗi kiểu `jobs-serving-471203`).
3. **Bật Billing:** menu trái → *Billing* → liên kết một *billing account*. Tài khoản mới được
   **$300 free trial** — kích hoạt để dùng. (Không bật billing thì hầu hết API không dùng được.)

## 1. Mở Cloud Shell (không cần cài đặt)

Trong Console, bấm icon **`>_`** (Activate Cloud Shell) góc trên phải. Một terminal Linux mở ra,
đã cài sẵn `gcloud`, `bq`, `git`, `python3` và **đã đăng nhập** bằng tài khoản của bạn.

Lấy code về Cloud Shell (thay bằng URL repo của bạn):
```bash
git clone <URL-repo-cua-ban> && cd jobs-serving-api/infra/gcp
```
> Cách khác (cài local, tuỳ chọn): cài [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
> → `gcloud auth login` → `gcloud auth application-default login`.

## 2. Điền cấu hình

```bash
cp config.example.sh config.sh
# Sửa config.sh: điền PROJECT_ID (bắt buộc). REGION đã đặt asia-southeast1 (Singapore).
nano config.sh      # hoặc dùng editor Cloud Shell
```
`config.sh` **không commit** (đã .gitignore) — chứa PROJECT_ID riêng của bạn.

## 3. Chạy các script THEO THỨ TỰ

| Bước | Lệnh | Tạo gì |
|---|---|---|
| Kiểm tra | `bash 00-preflight.sh` | Xác nhận đăng nhập + project + billing (chỉ đọc) |
| API | `bash 01-enable-apis.sh` | Bật các API cần dùng |
| BigQuery | `bash 10-bigquery-datasets.sh` | 2 dataset `jobs_staging`, `jobs_prod` |
| *(cụm sau)* | `20…`, `30…`, `40…`, `50…`, `60…` | SA/IAM, Artifact Registry, secrets, WIF, Redis |

Mỗi script in **PROJECT / REGION** ở đầu — nhìn kỹ trước khi để nó chạy tiếp, tránh nhầm project.

## Region

Đã khoá **`asia-southeast1` (Singapore)** — gần VN nhất, hỗ trợ đủ BigQuery + Cloud Run +
Memorystore. **`BQ_LOCATION` không đổi được sau khi tạo dataset** → muốn khác thì sửa `config.sh`
*trước* khi chạy `10-bigquery-datasets.sh`.

## Chi phí & dọn dẹp

- Free trial $300 dư sức cho giai đoạn này. **BigQuery**: corpus ~15k job rất nhỏ; `bq` query có
  trần `--maximum_bytes_billed` (đặt ở Phase 2/3). Dataset để không **không tốn tiền**.
- **Memorystore Redis KHÔNG thuộc Always Free** — tốn tiền kể cả khi idle. Script Redis (cụm 1.6)
  **viết sẵn nhưng bạn chỉ chạy khi cần demo**, và có `99-teardown.sh` để xoá sau khi xong.
- Budget alert (cụm 1.6) cảnh báo khi chi vượt ngưỡng `BUDGET_AMOUNT_USD`.

## An toàn

- Script chỉ tạo tài nguyên **idempotent** (có rồi thì bỏ qua). Không dùng JSON key — CI dùng
  Workload Identity Federation (cụm 1.5).
- `config.sh` và mọi secret **không vào git**.
