# ADR-004: ELT idempotent bằng truncate-then-load

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 3]
- Người quyết định: [Điền tên]

## Bối cảnh
Job ELT (`app/elt/build_silver.py`) bóc `job_post JOIN company` từ MySQL và ghi vào bảng `silver_jobs`
trong DuckDB. Job này sẽ chạy lại nhiều lần (mỗi lần làm tươi dữ liệu). Nếu ghi không cẩn thận, chạy 2
lần sẽ nhân đôi dữ liệu (76 → 152 dòng) — sai và tích rác.

## Quyết định
Nạp theo kiểu **truncate-then-load**: `CREATE OR REPLACE TABLE silver_jobs AS SELECT * FROM df`. Mỗi lần
chạy xoá sạch rồi dựng lại toàn bộ → **idempotent**, chạy bao nhiêu lần cũng cho cùng số dòng.

## Phương án đã cân nhắc
- **`INSERT INTO` (append)** — đơn giản nhất; nhưng nhân đôi dữ liệu mỗi lần chạy. Loại.
- **Upsert / MERGE theo `job_id`** — chuẩn cho dữ liệu lớn tăng dần, chỉ cập nhật dòng đổi; nhưng phức
  tạp hơn và thừa với 76 dòng. Để dành cho khi dữ liệu lớn/tải tăng dần thật.

## Hệ quả
- Tích cực: job an toàn để chạy lại theo lịch; logic đơn giản, dễ kiểm chứng (chạy 2 lần, so số dòng).
- Đánh đổi: dựng lại toàn bảng mỗi lần — không sao ở quy mô này; nếu sau này dữ liệu lớn, cân nhắc
  chuyển sang upsert (ghi vào nợ kỹ thuật).

## Kiểm chứng
[Điền sau khi chạy] `python -m app.elt.build_silver` hai lần: lần 1 = ____ dòng, lần 2 = ____ dòng
(phải bằng nhau).
