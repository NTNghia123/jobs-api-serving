> # ⚠️ TÀI LIỆU LEGACY (W3–W6) — KHÔNG áp dụng cho migration hiện tại
> Nội dung dưới đây mô tả contract CŨ (DuckDB backend, filter `country`, không có `posted_after`,
> các test đã bị xoá, "60 passed"…) và **sẽ không chạy đúng** với code hiện tại (migration Mongo → BigQuery,
> chỉ backend `fake` khả dụng ở Phase 0). Xem `README.md` + `docs/migration-mongo-bigquery-plan.md` để biết
> contract & cách chạy hiện hành.

# Bản hoàn thiện tới hết Tuần 4 — Hướng dẫn chạy & đọc code

> Đây là **bản clone đã implement sẵn Tuần 3 + Tuần 4** để bạn chạy và đọc-hiểu ngay tối nay.
> Project **gốc của bạn giữ nguyên**, không bị đụng. Khi rảnh, bạn tự implement lại vào gốc để luyện;
> bản này để tham chiếu và học.
>
> Trạng thái: **Tuần 4 / 8 — QueryValidator + an toàn truy vấn đã xong. 60 test xanh.**
> Toàn bộ Tuần 4 **chạy-hiểu được KHÔNG cần MySQL**. Chỉ pipeline ELT thật (Tuần 3) mới cần MySQL.

---

## 1. Chạy trong 3 phút (tối nay, không cần MySQL)

```bash
cd jobs-serving-api-w3w4-complete
python -m venv .venv && .venv\Scripts\activate      # Windows PowerShell
pip install -r requirements-dev.txt
copy .env.example .env                               # đã đặt sẵn backend=fake
```

Chạy test (bằng chứng mọi thứ hoạt động):
```bash
pytest -q            # kỳ vọng: 60 passed
```

Chạy API và mở docs:
```bash
uvicorn app.main:app --reload --port 8080
```
Mở http://localhost:8080/docs — bấm **Try it out** trên `POST /v1/jobs/search`.

> **Mặc định `backend=fake`**: API trả dữ liệu mẫu (mô phỏng đúng hình dạng thật), nên chạy ngay
> không cần cài MySQL/DuckDB. Đủ để đọc-hiểu Tuần 4. Muốn dữ liệu THẬT thì làm mục 4.

---

## 2. Đọc-hiểu Tuần 4 ngay (trọng tâm tối nay)

Tuần 4 = **thêm lớp `QueryValidator` đứng trước mọi truy vấn** + **chứng minh an toàn bằng test**.
Đọc theo thứ tự này:

| Bước | File | Đọc gì |
|---|---|---|
| 1 | `app/domain/validator.py` | Lớp QueryValidator. Đọc docstring đầu file: **vị trí của validator** so với Pydantic. Mỗi chính sách một method (`check_limit`, `check_filters`, `check_required`, `assert_safe_columns`, `suppress_if_small`, `validate_dimension/metric`). |
| 2 | `app/api/jobs.py` | Tìm 3 dòng đánh dấu `★ THÊM Ở TUẦN 4` — chỗ chèn `validator.validate_search(req)` vào đầu handler. |
| 3 | `tests/test_validator.py` | Test từng chính sách độc lập với Pydantic. |
| 4 | `tests/test_injection_safety.py` | **Test quan trọng nhất**: nạp `country = "Denmark'; DROP TABLE silver_jobs; --"` → 0 dòng + **bảng còn nguyên** = payload là dữ liệu, không phải mã SQL. |

Chạy riêng test Tuần 4 để xem nó xanh:
```bash
pytest tests/test_validator.py tests/test_injection_safety.py -v
```

Giải thích sâu (vì sao từng quyết định): đọc `Report/W4/W4-Huong-Dan-Thuc-Hien.md` và
`Report/W4/W4-BaoCao-Jobs-Serving-API.docx`. Quyết định thiết kế: `docs/adr/ADR-007`, `ADR-008`.

> **Điểm cần nắm để trả lời review:** với `/jobs/search`, phần lớn kiểm tra của validator **trùng**
> với Pydantic (phòng thủ theo chiều sâu). Giá trị RIÊNG của validator, không Pydantic nào thay được:
> allowlist cột trả ra (default-deny), k-anonymity (áp dụng Tuần 5), allowlist dimension/metric, cơ
> chế required-filter. Đừng nói quá vai trò của nó ở /jobs/search.

---

## 3. Bản đồ code — cái gì thuộc Tuần nào

**Tuần 3 (ELT MySQL→DuckDB + đọc dữ liệu thật):**
- `app/elt/build_silver.py` — job ELT: bóc job_post×company từ MySQL, làm sạch, ghi `silver_jobs` (idempotent).
- `app/infrastructure/warehouse/duckdb_jobs.py` — repository đọc DuckDB bằng SQL tham số + allowlist cột.
- `app/infrastructure/warehouse/sql/search_jobs.sql` — template SQL tách file.
- `app/api/deps.py` — thêm nhánh `backend=="duckdb"` (điểm swap duy nhất).
- `app/settings.py` — thêm `duckdb_path`, `mysql_url`.

**Tuần 4 (an toàn truy vấn):**
- `app/domain/validator.py` — lớp QueryValidator (MỚI).
- `app/api/jobs.py` — chèn validator (3 dòng `★`).

Các file có nhãn `★ THÊM Ở TUẦN {n}` và comment `# GIẢI THÍCH:` để bạn thấy chính xác cái gì mới và
vì sao. File gốc W2 (models, pagination, errors, catalog…) giữ nguyên.

---

## 4. (Tùy chọn) Chạy pipeline THẬT MySQL → DuckDB

Chỉ làm nếu muốn `/jobs/search` trả dữ liệu thật thay vì fake. Cần MySQL.

> **Lưu ý cổng 3306:** nếu máy bạn đã có sẵn MySQL cài native (Windows Service, ví dụ `MySQL84`) chiếm
> cổng 3306, container Docker sẽ **không** khởi động được ở cổng đó. Cách này dùng cổng **3307** cho
> container Docker để không đụng MySQL có sẵn (nếu máy bạn KHÔNG có MySQL native nào, dùng thẳng
> `3306:3306` cũng được, chỉ cần đổi số cổng tương ứng trong `.env`).

**a) Dựng MySQL + nạp dữ liệu (Docker):**
```bash
docker run --name jobportal-mysql -e MYSQL_ROOT_PASSWORD=rootpw \
  -e MYSQL_DATABASE=jobportal -p 3307:3306 -d mysql:8
# đợi ~15-20s cho tới khi log có dòng "ready for connections":
docker logs jobportal-mysql --tail 5
docker exec -i jobportal-mysql mysql -uroot -prootpw jobportal < database.sql
```

**b) Tạo user chỉ-đọc:**
```bash
docker exec -i jobportal-mysql mysql -uroot -prootpw -e "CREATE USER 'reader'@'%' IDENTIFIED BY 'reader_pw'; GRANT SELECT ON jobportal.* TO 'reader'@'%'; FLUSH PRIVILEGES;"
```

**c) Sửa `.env`** — đảm bảo đúng cổng 3307:
```
JOBS_API_MYSQL_URL=mysql+pymysql://reader:reader_pw@localhost:3307/jobportal
```

**d) Chạy ELT (cần pandas/sqlalchemy/pymysql — đã nằm trong requirements):**
```bash
python -m app.elt.build_silver     # kỳ vọng "silver_jobs: 100 dòng"
python -m app.elt.build_silver     # chạy lại vẫn 100 → idempotent
```
> **Đính chính:** tài liệu kế hoạch/báo cáo cũ ghi `job_post` có 76 dòng — con số đó **sai** so với
> `database.sql` thật đang có trong project này (đếm trực tiếp qua MySQL: 100 dòng job_post, 100
> company). Số đúng để tin là **100**, không phải 76. Các báo cáo cũ chưa được sửa lại theo số này.

**e) Đổi sang dữ liệu thật:** sửa `.env` → `JOBS_API_WAREHOUSE_BACKEND=duckdb`, khởi động lại uvicorn.
```bash
curl -s -X POST localhost:8080/v1/jobs/search \
  -H 'Content-Type: application/json' \
  -d '{"filters":{"seniority":"senior","salary_min":60000},"limit":3}'
```
Bây giờ `items` là tin thật từ DuckDB.

> Lưu ý: dừng uvicorn khi chạy `build_silver` (ELT mở kho ở chế độ ghi, API mở read-only) để tránh
> tranh khoá file DuckDB. Tương tự, đừng chạy song song với `scripts/xem_duckdb_ui.py` (mục 5).

---

## 5. Xem dữ liệu trực quan (MySQL và DuckDB)

### 5a. Xem MySQL qua phpMyAdmin (giao diện web)

**Container hiện tại (đã dựng bằng `docker run` tay, đang chạy):**
```bash
docker run --name jobportal-phpmyadmin --link jobportal-mysql:db -e PMA_HOST=db -p 8081:80 -d phpmyadmin
```

**Từ đầu / sau khi dọn sạch container cũ — dùng docker-compose thay vì nhớ 2 lệnh run:**
```bash
docker compose -f scripts/docker-compose.dev-tools.yml up -d
```
Gộp cả MySQL + phpMyAdmin vào 1 file (`scripts/docker-compose.dev-tools.yml`), tự nạp cả
`database.sql` **và** tự tạo user `reader` chỉ-đọc (`scripts/init-reader-user.sql`) ngay lần đầu —
qua `docker-entrypoint-initdb.d`, chạy theo thứ tự alphabet, idempotent (chỉ chạy khi ổ dữ liệu
rỗng). **Không cần chạy tay bước 4b nữa khi dùng compose.**
Dừng: `docker compose -f scripts/docker-compose.dev-tools.yml down` (thêm `--volumes` nếu muốn xoá
luôn dữ liệu MySQL đã nạp — mặc định KHÔNG xoá).

> **Lưu ý:** 2 file `.sql` chỉ tự chạy khi **ổ dữ liệu (volume) đang rỗng** — tức lần đầu tạo container
> qua compose. Vì bạn hiện đang giữ container cũ (tạo bằng `docker run`, không qua compose này), việc
> thêm `init-reader-user.sql` **không** tự áp dụng cho container đang chạy — nó chỉ có tác dụng ở lần
> `docker compose up` đầu tiên trên volume mới, sau này.

> **Đây KHÔNG phải** `docker-compose.yml` chính thức của Tuần 7 (kế hoạch gộp MySQL + chính API để
> deploy) — chỉ là compose tiện ích cho việc xem dữ liệu khi phát triển.

Mở `http://localhost:8081` — đăng nhập: Server `db` (dùng `docker run`) hoặc `mysql` (dùng compose,
đã tự điền), Username `root`, Password `rootpw`. Chọn database `jobportal` bên trái để xem từng bảng
(`job_post`, `company`, `users`...).

### 5b. Xem DuckDB qua UI tích hợp

```bash
python scripts/xem_duckdb_ui.py
```
Mở `http://localhost:4213`. Dừng bằng **Ctrl+C** trong terminal đó khi xem xong.

### 5c. Lần sau muốn xem lại thì làm gì

| | Có cần chạy lại lệnh không? |
|---|---|
| **MySQL + phpMyAdmin** (là container, chạy nền bền) | Nếu máy/Docker Desktop **chưa** khởi động lại: **không cần gì** — mở thẳng `http://localhost:8081`. Nếu **đã** khởi động lại (container dừng nhưng không mất): chạy `docker start jobportal-mysql` rồi `docker start jobportal-phpmyadmin` (KHÔNG dùng `docker run` lại — sẽ báo lỗi trùng tên vì container đã tồn tại), rồi mở lại link như cũ. |
| **DuckDB UI** (chỉ là script Python chạy tạm) | **Luôn phải chạy lại** `python scripts/xem_duckdb_ui.py` mỗi lần muốn xem — Ctrl+C hoặc đóng terminal là nó tắt hẳn, không tự sống lại. |

---

## 6. Đã kiểm chứng
- `pytest -q` → **60 passed** (hợp đồng W2 + DuckDB repo W3 + validator/injection W4).
- Test injection: payload `DROP TABLE` bị coi là dữ liệu, bảng còn nguyên → tham số hoá thật sự bảo vệ.
- Pipeline thật đã chạy: MySQL (Docker, cổng 3307) → `build_silver.py` → `analytics.duckdb` (100 dòng,
  idempotent qua 2 lần chạy) → user `reader` xác nhận chỉ đọc được, ghi bị từ chối.
- Cảnh báo `StarletteDeprecationWarning` về httpx là vô hại (chỉ là cảnh báo phiên bản).

---

## 7. Việc để sau (khi bạn tự implement vào project gốc)
1. Tự gõ lại W3 + W4 theo `Report/W3|W4/W3|W4-Huong-Dan-Thuc-Hien.md` để luyện.
2. Điền tên/ngày vào `docs/adr/ADR-001..008`.
3. Điền số liệu chạy thật vào các báo cáo `.docx` (ô `[Điền sau khi chạy]`) — nhớ dùng **100 dòng**,
   không phải 76 (xem đính chính ở mục 4d).
4. Tuần 5: gold table + caching + áp dụng k-anonymity ở `/market/metrics`.
