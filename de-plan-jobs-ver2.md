# Lộ trình thực tập Data Engineering — Jobs Serving API

> Tài liệu kế hoạch 8 tuần. Nguồn: chuyển từ `de-plan-jobs-ver2.html`, giữ đầy đủ nội dung để dùng làm đặc tả cho Claude Code.

## 0. Cách dùng lộ trình này

> Đọc theo thứ tự một lần để thấy bức tranh tổng thể, sau đó mỗi tuần quay lại đúng giai đoạn của tuần đó. Mỗi tuần có 5 phần cố định để bạn biết chính xác phải học gì và nộp gì.

- **Cấu trúc mỗi tuần**
- **Nguyên tắc xuyên suốt**

> **[Nguồn dữ liệu & môi trường phát triển]**
> Dự án dùng database thật `fulfilen/job-portal` (MySQL, ~100 công ty · ~100 tin tuyển dụng · 100 ứng viên · 1 admin). Đây là DB của một web PHP — tức là hệ thống nguồn OLTP, không phải kho phân tích. Vì vậy pipeline gồm ba chặng: MySQL (nguồn) → bước bóc & biến đổi (ELT) → DuckDB (kho phân tích cục bộ) → API đọc từ DuckDB. Giữ nguyên adapter pattern (Tuần 3) để sau này có thể đổi kho phân tích sang BigQuery mà không sửa code API. Học cục bộ hoàn toàn miễn phí; chỉ lên cloud khi triển khai thật (Tuần 7, tùy chọn).

## 1. Capstone: "Jobs Serving API"

> Bối cảnh: công ty vận hành một website tuyển dụng. Toàn bộ dữ liệu nằm trong một **database MySQL** (repo `fulfilen/job-portal`). Năm bảng nghiệp vụ: `job_post` (76 tin), `company` (100), `users` (100 ứng viên), `apply_job_post` (lượt ứng tuyển — hiện rỗng) và `admin`. Team AI cần dữ liệu này để gợi ý việc làm, benchmark lương và phân tích thị trường. Nhiệm vụ của bạn là xây tầng API ở giữa, để team AI không phải đụng vào SQL — và không bao giờ chạm được vào dữ liệu cá nhân của ứng viên (email, mật khẩu, ngày sinh, địa chỉ, số điện thoại).

> **[⚠️ Đã khảo sát schema thật — 4 giả định của Tuần 1 được đối chiếu]**
> Sau khi đọc database.sql, đây là bức tranh thật (khác đáng kể so với giả định ban đầu):
> - **Lương dễ hơn tưởng.** `minimumsalary`/`maximumsalary` khai báo `varchar` nhưng dữ liệu là *số nguyên sạch* (vd 43334 / 76458). Không có "thoả thuận", không có khoảng text → chỉ cần `CAST` sang số, không cần parse phức tạp.
> - **Không có `seniority`.** Chỉ có `experience` = số năm '1'..'5'. Bạn phải *tự suy ra* cấp bậc (vd 0–1 → junior, 2–3 → mid, 4–5 → senior). Đây trở thành một bài transform thật.
> - **Không có dữ liệu kỹ năng dùng được.** `users.skills` là text tự do và toàn `NULL`; `job_post` không có trường kỹ năng. → Use case "top kỹ năng đang tăng" (UC-3) **bị loại**.
> - **Ngày tháng vô dụng cho time-series.** Cả 76 tin đều có `createdat = 2017-10-10`. Metric "theo tháng" không có ý nghĩa với dữ liệu gốc → hoặc bỏ chiều thời gian, hoặc tự sinh ngày ngẫu nhiên khi transform để luyện tập.
> - **Địa điểm nằm ở công ty, không ở tin.** `job_post` không có cột thành phố; muốn lọc theo địa điểm phải `JOIN job_post → company` (địa danh là nước ngoài: Bulgaria, Vienna…). Một bài học join thật.

### Vì sao API, không đưa thẳng quyền truy vấn kho dữ liệu?

Đây là câu hỏi đắt giá nhất của cả chương trình. Nếu để team AI tự viết SQL vào kho:

- Mỗi lần đổi schema sẽ **làm vỡ** mọi consumer; API cho bạn một hợp đồng ổn định để che giấu thay đổi bên dưới.
- SQL tùy ý mở đường cho **injection**, full table scan ngốn tiền, và rò rỉ cột nhạy cảm — ở domain tuyển dụng, "cột nhạy cảm" là email/điện thoại nhà tuyển dụng, hồ sơ ứng viên và lương từng người.
- Logic nghiệp vụ (định nghĩa "tin còn hiệu lực", "lương quy đổi VND/tháng gross", "cấp bậc chuẩn hoá") bị **lặp lại và lệch nhau** ở mỗi consumer thay vì tập trung một chỗ.

Vì vậy API phải phơi bày **hoạt động nghiệp vụ (domain operations)** như `tìm tin tuyển dụng theo bộ lọc` hay `lấy chỉ số lương theo cấp bậc` — chứ không phải bảng thô của MySQL như `SELECT * FROM job_post`, càng không để lộ `users.email` hay `users.password`.

### Bộ endpoint sẽ xây dần qua 8 tuần

| Endpoint | Hoạt động nghiệp vụ | Xuất hiện ở tuần |
| --- | --- | --- |
| `GET /health` | Kiểm tra sống — endpoint duy nhất không cần auth | 2 |
| `GET /metadata` | Liệt kê filter/dimension hợp lệ cho consumer | 2 |
| `POST /jobs/search` | Tìm tin tuyển dụng theo bộ lọc nằm trong allowlist | 3–4 |
| `GET /market/metrics` | Chỉ số lương (trung vị, số tin) theo **cấp bậc** suy ra từ kinh nghiệm — không theo tháng (dữ liệu ngày đồng nhất) | 5 |
| + Lớp auth, rate-limit, logging, tracing bọc quanh tất cả | 6–8 |

## 2. Kiến trúc tổng thể

> Dữ liệu đi từ kho ra ngoài luôn phải qua các lớp kiểm soát. Đây là sơ đồ trạng thái cuối ở Tuần 8 — mỗi tuần bạn bật sáng thêm một khối.

- **MySQL · job-portal** (Hệ thống nguồn (OLTP)): DB web PHP thật (fulfilen) · ~100 công ty · ~100 tin · 100 ứng viên · cột lộn xộn, có PII
- **ELT / Transform** (Bóc & biến đổi): Job Python/SQL: đọc MySQL → làm sạch (chuẩn hoá lương, cấp bậc, thành phố) → ghi vào DuckDB · Tuần 3, 5
- **DuckDB (cục bộ)** (Kho phân tích): Bảng silver đã sạch + gold tiền-tổng-hợp chỉ số thị trường · đổi sang BigQuery được nhờ adapter
- **Warehouse Adapter** (Truy cập): Interface đổi được giữa DuckDB (dev/prod cục bộ) và BigQuery (nếu lên cloud) · Tuần 3
- **QueryValidator** (Cổng kiểm soát): Allowlist filter/cột, giới hạn dòng, bắt buộc filter ngày · Tuần 4
- **FastAPI Serving Layer** (Tầng phục vụ): Hợp đồng OpenAPI · model Pydantic · Auth · Cache · Rate limit · Structured logs · Tracing
- **Team AI** (Người dùng): Gọi API qua docs + client Python mẫu + test token · Tuần 8

Thành phần vận hành: Dev: Docker Compose (MySQL + API) · Prod (tùy chọn): Cloud Run · CI/CD: build → test → deploy · Secrets: .env → Secret Manager · Observability: OpenTelemetry

> **[⚠️ Quy tắc vàng]**
> Không có đường tắt. Mọi request của consumer phải đi xuyên qua QueryValidator trước khi chạm vào template SQL, và mọi template chỉ chứa tham số cho giá trị — còn tên bảng và tên cột phải nằm trong allowlist cứng trong code. Tham số SQL (dù ở MySQL, DuckDB hay BigQuery) không thay thế được identifier — quy tắc này đúng với mọi engine.

## 3. Lộ trình theo tuần

## Hiểu tầng phục vụ & thiết kế hợp đồng
*Giai đoạn 1 · Nền tảng*

### Mục tiêu

Phân biệt rạch ròi 4 lớp dữ liệu và lý giải được vì sao một API tốt phơi bày hoạt động nghiệp vụ. Viết được một bản đề xuất thiết kế API thuyết phục trước khi gõ dòng code đầu tiên.

### Khái niệm cốt lõi

- **Warehouse (kho):** nơi lưu và tính toán nặng trên dữ liệu lớn (BigQuery). Tối ưu cho phân tích, không tối ưu cho phục vụ độ trễ thấp.
- **Semantic layer (tầng ngữ nghĩa):** nơi định nghĩa thống nhất các *metric* và *dimension* nghiệp vụ ("tin còn hiệu lực" = chưa hết hạn & chưa bị đóng; "lương quy đổi" = trung điểm khoảng lương, quy về VND/tháng gross) để mọi nơi hiểu giống nhau.
- **API layer:** phơi bày hoạt động nghiệp vụ qua HTTP với hợp đồng ổn định, có kiểm soát truy cập và an toàn.
- **BI dashboard:** nơi *con người* tiêu thụ dữ liệu bằng mắt. Khác với API phục vụ cho *máy / hệ thống khác*.

> **[Tại sao là domain operations?]**
> Consumer của bạn là team AI. Họ cần response có kiểu rõ ràng, ổn định, semantics dễ đoán để đưa vào tool-call của LLM. Một endpoint /jobs/search với schema cố định dễ tích hợp hơn vô hạn lần so với việc trao cho họ quyền viết SQL tự do.

### Việc cần làm

- **Import DB và khảo sát schema thật trước tiên.** Import `database.sql` vào MySQL/phpMyAdmin, rồi chạy bộ câu khảo sát (xem callout bên dưới) để biết: tên các bảng nghiệp vụ, các cột của bảng tin tuyển dụng, lương đang lưu dạng gì, có sẵn `seniority`/bảng kỹ năng không.
- Từ kết quả khảo sát, **xác nhận hoặc bác bỏ 4 giả định** trong báo cáo Tuần 1 (seniority, định dạng lương, bảng kỹ năng, bảng ánh xạ). Ghi lại giả định nào sai để điều chỉnh use case.
- Vẽ sơ đồ luồng dữ liệu *bắt đầu từ MySQL nguồn*, qua chặng ELT, tới DuckDB rồi ra API.
- Chốt use case theo **dữ liệu thật sự có**: (UC-1) tìm tin theo lương / cấp bậc / kinh nghiệm / địa điểm công ty; (UC-2) benchmark lương theo cấp bậc. Bỏ use case "top kỹ năng" vì không có dữ liệu kỹ năng dùng được.
- Từ use case suy ra endpoint, không suy ngược từ bảng có sẵn.
- Chốt sớm phần **phi chức năng**: mục tiêu độ trễ (vd p95 < 800ms cho `/jobs/search`), ngân sách timeout, và danh sách trường *không bao giờ* được trả ra (PII: email, mật khẩu hash, liên hệ).

> **[Kết quả khảo sát schema (đã chạy trên DB thật)]**
>
> ```
> -- 5 bảng nghiệp vụ + 3 bảng tra cứu địa lý (cities ~48k dòng → file 1.44MB)
> job_post: 76 dòng -- id_jobpost, id_company, jobtitle, description, -- minimumsalary, maximumsalary (varchar, giá trị là số), -- experience ('1'..'5' năm), qualification, createdat company: 100 dòng -- name, companyname, country, state, city, ← ĐỊA ĐIỂM Ở ĐÂY -- contactno, email ← PII, website, logo users: 100 dòng -- firstname, lastname, email, password(base64), -- dob, address, contactno ← PII NẶNG; skills=text NULL apply_job_post: 0 dòng -- id_jobpost, id_company, id_user, status ← hiện RỖNG admin: 1 dòng ``` Không có: cột seniority, ngành nghề, thành phố ở tin, hay bảng kỹ năng. Ba thứ này bạn phải tự tạo ở bước transform.

### Mảnh project tuần này

Một **bản đề xuất thiết kế API dài một trang** theo khung sau:

```text
# API Design Proposal — Jobs Serving API
1. Consumers      : team AI (agent gợi ý việc làm, công cụ phân tích thị trường lao động)
2. Use cases     : - tìm tin tuyển dụng theo thành phố/ngành nghề/cấp bậc/lương
                  - lấy chỉ số thị trường lao động theo ngành nghề theo tháng
3. Endpoints     : POST /jobs/search · GET /market/metrics
                  GET /metadata · GET /health
4. Request       : { filters: {city, job_function, seniority, salary_min, ...},
                    limit, page_token }
5. Response      : { items: [...], total, next_page_token, as_of }
6. SLO          : p95 < 800ms · availability 99.5% · timeout truy vấn 15s
7. Non-goals     : không cho SQL tự do · không ghi dữ liệu (read-only)
                  không phơi bày PII ứng viên hay liên hệ nhà tuyển dụng
```

### Deliverable

- [ ] Bản đề xuất 1 trang: consumers, use cases, endpoints, ví dụ request/response.
- [ ] Sơ đồ luồng dữ liệu từ MySQL nguồn → ELT → DuckDB → API.
- [ ] Bảng đối chiếu schema thật (đã có): lương=số sạch ✓ · không seniority ✗ · không skills ✗ · ngày đồng nhất ✗ · địa điểm ở company.
- [ ] Giải thích được bằng lời "vì sao không trao quyền SQL trực tiếp".

## Thiết kế hợp đồng API với FastAPI
*Giai đoạn 1 · Nền tảng*

### Mục tiêu

Biến bản đề xuất thành một hợp đồng thực thi được: skeleton FastAPI có docs tự sinh, model request/response gắn kiểu, định dạng lỗi nhất quán.

### Khái niệm cốt lõi

- **REST cơ bản:** resource, method, status code; khi nào dùng path param vs query/body.
- **OpenAPI:** hợp đồng máy-đọc-được; FastAPI sinh tự động — đây sẽ là tài liệu chính cho team AI.
- **Pydantic (v2):** validate input/output theo schema, ép kiểu, từ chối field lạ.
- **Error format:** một envelope lỗi thống nhất (`{error_code, message, request_id}`) thay vì mỗi nơi một kiểu.
- **Versioning & pagination:** prefix `/v1`; phân trang bằng *keyset/cursor* (page_token) thay vì offset — vì offset trên kho dữ liệu lớn rất tốn kém.

### Mảnh project tuần này

```python
from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="Jobs Serving API", version="1.0")

class Health(BaseModel):
    status: str = "ok"

class SearchRequest(BaseModel):
    city: str | None = None
    seniority: str | None = None             # suy ra từ experience: junior|mid|senior
    experience_max: int | None = Field(None, ge=0, le=50)  # số năm, từ job_post.experience
    salary_min: int | None = Field(None, ge=0)   # từ minimumsalary (CAST varchar→int)
    country: str | None = None               # địa điểm lấy từ company (JOIN)
    limit: int = Field(20, ge=1, le=100)
    page_token: str | None = None

@app.get("/health", response_model=Health)
def health():
    return Health()

@app.get("/metadata")
def metadata():
    # trả về filter/dimension hợp lệ cho consumer tự khám phá
    return {"filters": ["seniority", "experience_max",
                        "salary_min", "country"]}  # chỉ những gì dữ liệu thật hỗ trợ
```

> **[Mẹo]**
> Chạy uvicorn main:app --reload rồi mở /docs — Swagger UI tự sinh từ model Pydantic. Đây vừa là công cụ test vừa là tài liệu sẽ giao cho team AI ở Tuần 8.

### Deliverable

- [ ] Skeleton FastAPI chạy được với `/health` và `/metadata`.
- [ ] Docs OpenAPI tự sinh tại `/docs`.
- [ ] Model request/response gắn kiểu + envelope lỗi nhất quán.

## ELT từ MySQL & tích hợp truy vấn
*Giai đoạn 2 · Truy vấn an toàn*

### Mục tiêu

Viết bước ELT đầu tiên: bóc dữ liệu tin tuyển dụng từ **MySQL nguồn**, làm sạch tối thiểu, ghi vào một bảng *silver* trong **DuckDB**. Rồi nối `/jobs/search` đọc từ DuckDB qua một adapter đổi được, dùng SQL có tham số. Endpoint bắt đầu trả dữ liệu thật — nhưng chỉ với bộ lọc trong allowlist.

### Khái niệm cốt lõi

- **ELT vs ETL:** ở đây bóc dữ liệu (Extract) từ MySQL, nạp (Load) vào DuckDB, rồi biến đổi (Transform) bằng SQL ngay trong DuckDB. Tách rạch ròi *hệ thống nguồn* (chỉ đọc, không đụng chạm) khỏi *kho phân tích* (nơi ta tự do dựng bảng sạch).
- **Idempotent load:** chạy lại job ELT nhiều lần phải cho cùng kết quả (dùng *truncate-then-load* hoặc *upsert*), không nhân đôi dữ liệu.
- **Warehouse adapter pattern:** một interface (`run_query`) để code API không phụ thuộc backend; dev/prod cục bộ dùng DuckDB, đổi sang BigQuery nếu lên cloud mà không sửa handler.
- **Parameterized SQL:** truyền giá trị qua tham số, không bao giờ nối chuỗi — nền tảng chống injection, áp dụng cho cả bước đọc MySQL lẫn đọc DuckDB.
- **Tổ chức SQL template & query timeout:** tách câu SQL ra file `.sql`; luôn đặt thời gian chờ tối đa, trả lỗi sạch khi quá hạn.

### Mảnh project tuần này

```python
from abc import ABC, abstractmethod

class WarehouseAdapter(ABC):
    @abstractmethod
    def run_query(self, sql: str, params: dict, timeout_s: int) -> list[dict]: ...

class DuckDBAdapter(WarehouseAdapter):     # kho phân tích, dev/prod cục bộ
    def run_query(self, sql, params, timeout_s=30):
        # DuckDB nhận tham số theo tên: WHERE city_code = $city
        return self.conn.execute(sql, params).fetchall()

class BigQueryAdapter(WarehouseAdapter):    # dùng khi lên cloud (tùy chọn)
    def run_query(self, sql, params, timeout_s=30):
        job_config = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("salary_min", "INT64", params["salary_min"])])
        job = client.query(sql, job_config=job_config)
        return [dict(r) for r in job.result(timeout=timeout_s)]
```

Bước ELT tối giản — bóc từ MySQL nguồn, ghi vào DuckDB (chạy lại được nhiều lần):

```python
import duckdb, pandas as pd
from sqlalchemy import create_engine

src = create_engine("mysql+pymysql://reader:***@localhost/jobportal")  # user CHỈ-ĐỌC
# JOIN tin với công ty để lấy địa điểm (tin không có cột thành phố)
df = pd.read_sql("""
    SELECT j.id_jobpost, j.jobtitle, j.description,
           CAST(j.minimumsalary AS UNSIGNED) AS salary_min,   -- varchar → số
           CAST(j.maximumsalary AS UNSIGNED) AS salary_max,
           CAST(j.experience   AS UNSIGNED) AS years_exp,
           j.qualification, j.createdat,
           c.companyname, c.country, c.state, c.city           -- địa điểm từ company
    FROM job_post j JOIN company c ON c.id_company = j.id_company
""", src)  # KHÔNG lấy c.email, c.password, c.contactno — PII

# suy ra cấp bậc từ số năm (dữ liệu không có sẵn seniority)
df["seniority"] = pd.cut(df.years_exp, [-1,1,3,99],
                         labels=["junior","mid","senior"])

con = duckdb.connect("analytics.duckdb")
con.execute("CREATE OR REPLACE TABLE silver_jobs AS SELECT * FROM df")  # idempotent
```

Map filter người dùng → cột thật qua một allowlist cứng (sẽ siết chặt thêm ở Tuần 4):

```
ALLOWED_FILTERS = {                # cột trong silver_jobs (DuckDB), không phải cột MySQL thô
    "seniority":      ("seniority",   "VARCHAR"),
    "experience_max": ("years_exp",   "INTEGER"),
    "salary_min":     ("salary_min",  "INTEGER"),
    "country":        ("country",     "VARCHAR"),
}  # khóa do user gửi → cột nội bộ; không nhận tên cột tùy ý
```

### Deliverable

- [ ] Job ELT bóc tin tuyển dụng từ MySQL → bảng `silver_jobs` trong DuckDB; chạy lại 2 lần cho cùng số dòng (idempotent).
- [ ] `/jobs/search` trả dữ liệu thật đọc từ DuckDB qua template có tham số.
- [ ] Chỉ dùng các filter trong allowlist; filter lạ bị từ chối.
- [ ] Adapter đổi được giữa DuckDB và BigQuery mà không sửa handler.

## An toàn truy vấn & kiểm soát chi phí
*Giai đoạn 2 · Truy vấn an toàn*

### Mục tiêu

Đóng mọi cánh cửa cho SQL injection và cho truy vấn ngốn tiền. Bổ sung một lớp `QueryValidator` đứng trước mọi truy vấn.

### Khái niệm cốt lõi

- **Không bao giờ nhận SQL tùy ý.** Chỉ ráp truy vấn từ các thành phần đã được allowlist.
- **Tham số chỉ cho giá trị.** Tham số SQL giúp chống injection nhưng *không* thay thế được tên bảng/cột/identifier → identifier phải allowlist riêng. Đúng với MySQL, DuckDB và BigQuery.
- **Bộ lọc bắt buộc:** ép phải có filter ngày để tránh quét toàn bộ lịch sử.
- **Giới hạn:** chiếu cột (column projection) + giới hạn số dòng — áp dụng cho mọi engine. Riêng khi lên BigQuery mới có thêm `maximum_bytes_billed` và **dry run** để chặn/ước lượng chi phí theo byte.

> **[⚠️ Hiểu lầm phổ biến]**
> LIMIT không phải cơ chế kiểm soát chi phí. Trên BigQuery, với bảng không phân cụm, engine vẫn quét toàn bộ cột được chọn rồi mới cắt dòng. Ở DuckDB cục bộ thì "chi phí" là thời gian/bộ nhớ chứ không phải tiền, nhưng nguyên tắc vẫn đúng: muốn nhanh phải lọc sớm và chỉ chọn đúng cột cần — thói quen này để sẵn cho ngày lên cloud.

### Mảnh project tuần này

```python
class QueryValidator:
    ALLOWED_DIMENSIONS = {"seniority", "country"}   # chỉ chiều dữ liệu thật có
    ALLOWED_METRICS    = {"median_salary", "posting_count"}  # bỏ applications: apply rỗng
    MAX_LIMIT          = 100
    MIN_GROUP_SIZE     = 5   # chống suy ngược lương của một người cụ thể

    def validate(self, req: SearchRequest):
        if req.limit > self.MAX_LIMIT:
            raise ValueError("limit vượt mức cho phép")
        # Lưu ý: DB gốc có createdat đồng nhất nên "filter ngày" không lọc được gì.
        # Vẫn giữ trần số dòng + chỉ-chọn-cột làm cơ chế kiểm soát chính ở đây.
        for f in req.active_filters():
            if f not in ALLOWED_FILTERS:
                raise ValueError(f"filter không hợp lệ: {f}")
```

> **[⚠️ Riêng với dữ liệu tuyển dụng: PII & lương]**
> Đây là điểm khác lớn nhất so với một domain vô hại. Kho tuyển dụng chứa dữ liệu cá nhân:
              hồ sơ/lượt ứng tuyển của ứng viên, email & số điện thoại người đăng tin, mức lương. Vì vậy:
              (1) allowlist cột phải
> liệt kê cột được phép trả ra, không phải liệt kê cột bị cấm — mặc định là từ chối;
              (2) chỉ số lương tổng hợp cần
> ngưỡng chống suy ngược (k-anonymity): ô nào tổng hợp từ < 5 tin
              hoặc < 3 công ty thì trả
> null kèm lý do, vì "lương trung bình ngành X ở công ty Y" chính là lương của một người;
              (3) không log giá trị filter chứa PII. Ở Việt Nam, đối chiếu Nghị định 13/2023/NĐ-CP về bảo vệ dữ liệu cá nhân.
> Với DB này cụ thể: bảng ứng viên chứa email và mật khẩu đã hash; bảng công ty có thể chứa email/điện thoại liên hệ. Cách chắc chắn nhất để không rò rỉ: bước ELT ở Tuần 3 chỉ bóc các cột an toàn sang DuckDB, để cột PII không bao giờ rời khỏi MySQL — allowlist ở tầng API trở thành lớp phòng thủ thứ hai, không phải lớp duy nhất.

### Deliverable

- [ ] `QueryValidator`: allow dimensions, allow metrics, max limit, bắt buộc filter ngày/snapshot.
- [ ] Allowlist cột trả ra loại trừ toàn bộ PII; metric lương có ngưỡng k-anonymity.
- [ ] Đặt `maximum_bytes_billed` + dùng dry run ước lượng chi phí.
- [ ] Test: input độc hại / filter lạ / thiếu filter ngày đều bị từ chối.

## Tối ưu hiệu năng & chi phí
*Giai đoạn 3 · Hiệu năng & Bảo mật*

### Mục tiêu

Làm API nhanh và rẻ. Thêm endpoint `/market/metrics` trả **lương trung vị + số tin theo cấp bậc** (suy từ kinh nghiệm) đọc từ gold table và có caching. Không dùng chiều tháng vì ngày trong DB đồng nhất.

### Khái niệm cốt lõi

- **Chuẩn hoá lương — công việc thật của tuần này.** Đây là lúc xử lý cột lương text tự do của DB: tách khoảng "15000-20000", đổi "Negotiable"/rỗng thành `null` (loại khỏi mẫu), quy về một đơn vị. Logic này sống ở *bước transform*, dồn kết quả vào gold table — không rải rác trong API.
- **Gold table:** tiền-tổng-hợp chỉ số thị trường (tháng × ngành × thành phố, và cấp bậc nếu suy ra được) vào bảng nhỏ trong DuckDB → API đọc cực nhanh.
- **Tránh `SELECT *` & lọc sớm:** chỉ chọn đúng cột cần; thói quen giữ cho ngày lên BigQuery (nơi nó thành tiền thật).
- **Caching:** cache response cho truy vấn lặp lại (TTL theo độ tươi dữ liệu).
- **Pagination & async job:** phân trang keyset; truy vấn nặng dùng mẫu bất đồng bộ (trả job id rồi cho poll).

### Mảnh project tuần này

```python
from cachetools import TTLCache
cache = TTLCache(maxsize=512, ttl=300)  # cache 5 phút

@app.get("/market/metrics")
def market_metrics(seniority: str):
    if seniority in cache:
        return cache[seniority]
    # đọc từ gold table đã "nướng" sẵn — không quét silver mỗi request
    sql = "SELECT median_salary, posting_count, as_of " \
          "FROM gold_salary_by_seniority WHERE seniority = $seniority"
    row = adapter.run_query(sql, {"seniority": seniority})[0]
    if row["posting_count"] < MIN_GROUP_SIZE:   # k-anonymity: nhóm quá nhỏ
        row["median_salary"] = None
    cache[seniority] = row
    return row
```

> **[⚠️ Cảnh báo dữ liệu nhỏ]**
> Chỉ có 76 tin chia cho 3 cấp bậc → mỗi nhóm ~25 tin. Ngưỡng k-anonymity vẫn nên bật để bạn thấy cơ chế hoạt động, nhưng đừng ngạc nhiên nếu vài nhóm hẹp bị ẩn. Đây là lý do dữ liệu thật nhỏ vẫn đủ để luyện, miễn bạn hiểu giới hạn của nó.

> **[Bài học]**
> Gold table được "nướng" sẵn bằng một job tổng hợp chạy định kỳ — ở quy mô này chỉ cần một script Python/SQL chạy sau mỗi lần ELT (sau này có thể nâng lên dbt hoặc scheduled query). API chỉ đọc kết quả đã nướng sẵn; chuyển công việc nặng ra khỏi đường phục vụ là chiến lược tối ưu mạnh nhất, và cũng là chỗ đặt logic k-anonymity cho lương.

### Deliverable

- [ ] `/market/metrics` trả lương trung vị theo cấp bậc, đọc từ gold table.
- [ ] Có caching với TTL; đo được độ trễ giảm khi cache hit.
- [ ] Mọi truy vấn chỉ chọn cột cần, không còn `SELECT *`.

## Xác thực & kiểm soát truy cập
*Giai đoạn 3 · Hiệu năng & Bảo mật*

### Mục tiêu

Bảo vệ mọi endpoint trừ `/health`, nhận diện được từng client, và giới hạn tốc độ gọi theo từng client.

### Khái niệm cốt lõi

- **API key vs JWT:** API key đơn giản, hợp cho internal; JWT mang được claim/quyền và hạn dùng. API Gateway có thể xác thực JWT *trước* khi chuyển request về backend.
- **Least privilege ở cả hai chặng:** job ELT kết nối **MySQL nguồn** bằng một user MySQL **chỉ-đọc** (`GRANT SELECT`, không sửa/xoá), và tốt nhất chỉ trên các bảng/cột an toàn. API thì không nối thẳng MySQL — nó chỉ đọc DuckDB đã lọc sẵn PII. Hai ranh giới, không phải một.
- **Per-client rate limit:** mỗi client một hạn mức (token bucket) để một consumer không làm nghẽn cả hệ thống.
- **Client identity in logs:** ghi danh tính client vào log để truy vết và quy trách nhiệm.

### Mảnh project tuần này

```python
from fastapi import Depends, Header, HTTPException

def require_client(x_api_key: str = Header(...)) -> str:
    client = API_KEYS.get(x_api_key)
    if client is None:
        raise HTTPException(401, "invalid api key")
    if rate_limiter.exceeded(client):
        raise HTTPException(429, "rate limit")
    return client  # dùng làm client_id để đưa vào log

@app.post("/jobs/search")
def search(req: SearchRequest, client: str = Depends(require_client)):
    log.info("search", extra={"client_id": client})
    ...
```

### Deliverable

- [ ] Mọi endpoint trừ `/health` đều yêu cầu xác thực.
- [ ] Danh tính client được ghi vào log mỗi request.
- [ ] Rate limit theo client; job ELT dùng user MySQL chỉ-đọc (`GRANT SELECT`).

## Triển khai & độ tin cậy
*Giai đoạn 4 · Vận hành*

### Mục tiêu

Đóng gói mọi thứ chạy được bằng một lệnh. **Ưu tiên đường cục bộ trước:** một `docker-compose.yml` dựng cả MySQL nguồn + API, để bất kỳ ai `docker compose up` là có hệ thống chạy. Việc lên **Cloud Run là tùy chọn** — làm nếu muốn luyện triển khai cloud, nhưng không bắt buộc cho một dự án học tập.

### Khái niệm cốt lõi

- **Docker:** image gọn (multi-stage, base slim), chạy bằng user không phải root.
- **Cloud Run:** tự co giãn theo số request đến; tinh chỉnh *số request đồng thời mỗi instance (concurrency)* ảnh hưởng trực tiếp đến độ trễ và chi phí; cân nhắc min-instance để giảm cold start.
- **Docker Compose (cục bộ):** gom MySQL + API vào một file, dùng healthcheck để API chờ MySQL sẵn sàng rồi mới chạy ELT khởi tạo.
- **Config & secrets:** cấu hình qua biến môi trường; bí mật để trong `.env` (cục bộ) hoặc Secret Manager (cloud), không hard-code.
- **Timeout & CI/CD:** đặt request timeout; pipeline build → test → (tùy chọn) deploy, tách staging và prod.

### Mảnh project tuần này

```dockerfile
# Dockerfile (multi-stage, non-root)
FROM python:3.12-slim AS base
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m appuser
USER appuser
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```yaml
# .github/workflows/deploy.yml (rút gọn)
steps:
  - run: pytest                  # test trước khi deploy
  - run: gcloud builds submit     # build image
  - run: gcloud run deploy api --region=... --no-allow-unauthenticated
```

### Deliverable

- [ ] `docker compose up` dựng được cả MySQL + API chạy hoàn chỉnh trên máy sạch.
- [ ] CI/CD cơ bản: chạy test → build (→ deploy Cloud Run nếu chọn lên cloud).
- [ ] Không có bí mật hard-code; request timeout & concurrency hợp lý.

## Observability & bàn giao cho team AI
*Giai đoạn 4 · Bàn giao*

### Mục tiêu

Làm API quan sát được và bàn giao trọn gói: log có cấu trúc, request ID, metric độ trễ, tracing, tài liệu + client Python mẫu + test token để team AI gọi được ngay.

### Khái niệm cốt lõi

- **Structured logs:** log dạng JSON với `request_id`, `client_id`, độ trễ, mã lỗi → truy vấn và lọc được.
- **Warehouse query stats:** ghi lại bytes quét / thời gian truy vấn của mỗi request để theo dõi chi phí.
- **Tracing:** OpenTelemetry có instrumentation cho FastAPI để trace request HTTP đầu-cuối.
- **Handoff:** tài liệu API (từ OpenAPI), client Python mẫu, và test token cho team AI.

### Mảnh project tuần này

```python
# client Python mẫu giao cho team AI
import requests
BASE = "https://jobs-api.example.com/v1"

def search_jobs(seniority, salary_min, token):
    r = requests.post(
        f"{BASE}/jobs/search",
        headers={"X-API-Key": token},
        json={"filters": {"seniority": seniority, "salary_min": salary_min},
              "limit": 20},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()
```

### Deliverable cuối — Demo bàn giao

- [ ] Structured logs + request ID + metric độ trễ + query stats.
- [ ] Tracing OpenTelemetry cho request HTTP.
- [ ] Tài liệu API + client Python mẫu + test token.
- [ ] **Demo:** team AI gọi được API chỉ bằng tài liệu, client mẫu và test token.

## 4. Bảng tự đánh giá năng lực

> Cuối mỗi giai đoạn, tự chấm mình theo 3 mức. Mục tiêu là leo từ Biết lên Làm được rồi Giải thích & bảo vệ được lựa chọn.

| Năng lực | Biết | Làm được | Làm chủ |
| --- | --- | --- | --- |
| Phân lớp dữ liệu & thiết kế API hướng nghiệp vụ | Nêu được 4 lớp | Vẽ kiến trúc cho 1 use case mới | Bảo vệ được vì sao chọn domain op |
| Hợp đồng API (OpenAPI, Pydantic) | Hiểu schema | Tự thiết kế endpoint mới có docs | Versioning & pagination hợp lý |
| Truy vấn kho an toàn | Biết parameterized SQL | Dựng adapter + template | Lý giải vì sao identifier phải allowlist |
| Kiểm soát chi phí truy vấn | Biết tránh `SELECT *` | Dùng gold table + dry run | Phân tích cost của 1 truy vấn bất kỳ |
| Auth & access control | Phân biệt key/JWT | Bảo vệ endpoint + rate limit | Thiết kế least-privilege cho cả hệ |
| Triển khai & CI/CD | Build được image | Deploy Cloud Run + pipeline | Tinh chỉnh concurrency/cost/latency |
| Observability | Biết structured log | Thêm request ID + tracing | Dựng dashboard sức khỏe API |

## 5. Mở rộng nếu còn thời gian (stretch goals)

> Khi đã hoàn thành lõi, đây là những hướng nâng cấp gây ấn tượng và thực sự hữu ích cho một team AI.

- **Endpoint `/jobs/{id}`** — Trả chi tiết một tin tuyển dụng — bổ sung tra cứu theo khóa chính, dạy về path param & xử lý 404 sạch.
- **Matching bằng mô tả công việc** — Vì cột kỹ năng trống, hãy tạo embedding từ `job_post.description` để tìm tin "tương tự ngữ nghĩa" — vẫn dạy đúng kỹ thuật vector search, chỉ đổi nguồn văn bản.
- **Semantic layer với dbt** — Đưa định nghĩa metric/dimension & gold table vào dbt model, có test dữ liệu & lineage.
- **Contract test & load test** — Test hợp đồng dựa trên OpenAPI; load test để xác định ngưỡng concurrency & đặt SLO.
- **Rate limit phân tán** — Chuyển token bucket sang Redis để hoạt động đúng khi Cloud Run chạy nhiều instance.
- **Async job cho truy vấn nặng** — Trả `request_id` rồi cho poll kết quả — mẫu chuẩn cho báo cáo thị trường lao động tốn thời gian (tránh trùng tên với "job" nghĩa là tin tuyển dụng).

### Lời khuyên cuối

- **Mỗi tuần kết thúc bằng một thứ chạy được.** Đừng để sản phẩm "vỡ" giữa chừng — luôn giữ nó ở trạng thái demo được.
- **Viết test ngay từ Tuần 3.** Đặc biệt cho QueryValidator: input độc hại, filter lạ, thiếu filter ngày phải đều bị chặn.
- **Ghi nhật ký quyết định.** Mỗi lần chọn (vd: keyset vs offset, API key vs JWT), ghi 2–3 dòng lý do — đây là thứ sẽ giúp bạn ở buổi review & phỏng vấn sau này.
- **Luôn nghĩ từ phía consumer.** Sau mỗi endpoint, tự hỏi: "team AI có gọi được chỉ với tài liệu, mà không cần hỏi mình không?"

## 6. Bổ sung sau review kỹ thuật

> Bản gốc bao phủ tốt curriculum, nhưng còn thiếu vài thứ mà một hệ thống thật luôn cần. Đây là các điểm nên chèn vào đúng tuần tương ứng — không phải làm thêm ở cuối.

- **T1 · Chốt SLO trước khi code** — Không có mục tiêu p95/availability thì "tuần tối ưu hiệu năng" không có gì để đo. Viết SLO vào ngay bản đề xuất API.
- **T1 · BigQuery không phải DB phục vụ** — BigQuery có độ trễ tối thiểu cỡ 0,5–2s và không làm full-text search/ranking tốt — trong khi tìm việc chủ yếu là tìm kiếm văn bản có xếp hạng. Hãy viết một ADR: chấp nhận độ trễ đó, hay đẩy bảng gold sang Postgres/OpenSearch để phục vụ?
- **T2 · Kéo structured log lên sớm** — `request_id` + log JSON tốn 20 dòng code nhưng giúp bạn debug suốt 6 tuần sau. Tuần 8 chỉ nên còn tracing, metric và dashboard.
- **T2–3 · Test từ đầu, có fake adapter** — Adapter pattern cho bạn `FakeAdapter` miễn phí: test được toàn bộ API mà không chạm BigQuery. Thêm contract test sinh từ OpenAPI.
- **T4 · `page_token` cũng là input của user** — Cursor phải được ký (HMAC) hoặc mã hoá và validate lại; nếu không, người gọi sửa token để đổi cột sắp xếp/điều kiện là bạn thủng đúng lớp vừa dựng.
- **T5 · Cache & rate limit trong process là sai trên Cloud Run** — Mỗi instance một bộ nhớ riêng: 10 instance = hạn mức gấp 10 và cache hit rate tệ. Dùng Memorystore/Redis, hoặc chấp nhận có ý thức và ghi vào ADR.
- **T5 · Load test để con số có thật** — k6 hoặc Locust, đo p50/p95/p99 trước và sau khi thêm gold table + cache. "Nhanh hơn" không phải kết luận, nó là số đo.
- **T5 · Gắn nhãn chi phí theo client** — Đặt label `client_id` cho mỗi query BigQuery để quy chi phí về từng consumer, cộng thêm cảnh báo ngân sách. Đây là thứ khiến bạn nói chuyện được với sếp, không chỉ với code.
- **T3–8 · Độ tươi dữ liệu là một phần hợp đồng** — Trả `as_of` trong mọi response và cảnh báo khi bảng gold trễ. Consumer LLM không thể tự biết dữ liệu đã cũ 3 ngày.
- **T6 · API key trong dict chỉ là đồ chơi** — Lưu *hash* của key, có hạn dùng và quy trình xoay vòng. Nếu consumer nằm trong cùng GCP, xác thực service-to-service bằng OIDC token của Cloud Run tốt hơn hẳn API key.
- **T7 · Ngân sách timeout phải lồng nhau** — client > Cloud Run request timeout > timeout truy vấn kho. Client mẫu để 30s trong khi query cho phép 30s là công thức để mọi lỗi đều thành lỗi treo.
- **Xuyên suốt · Chất lượng mã & phụ thuộc** — Pin phiên bản (uv/poetry), ruff + mypy trong CI, quét lỗ hổng bằng `pip-audit`/Trivy. "Dependency vulnerabilities" là một mục bảo mật thật, không phải hình thức.

> **[⚠️ Câu hỏi tự trả lời trước buổi review cuối]**
> 1) Nếu bảng gold trễ 1 ngày, consumer biết bằng cách nào? 2) Một client gọi 1000 req/phút thì ai chịu hoá đơn và hệ thống hỏng ở đâu trước? 3) Vì sao `POST /jobs/search` là POST dù nó chỉ đọc, và điều đó làm mất gì về caching? 4) Muốn thêm filter `company_size`, phải sửa những chỗ nào và có phải bump version không?
