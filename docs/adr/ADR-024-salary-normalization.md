# ADR-024: Chuẩn hoá lương — đơn vị suy từ raw, khoá VND/USD/tháng, trần sanity

- Trạng thái: Đã chấp nhận
- Ngày: 2026-09-13
- Người quyết định: [Điền tên]
- Liên quan: ADR-019 (data contract), ADR-010 (lương đại diện median); impl `app/elt/serving/salary.py`

## Bối cảnh
Scraper normalize sẵn `JobSalary{raw, min, max, currency, unit}` nhưng dữ liệu VNW **mâu thuẫn
nội tại** (đối chiếu Mongo thật ~15k job):
- `salaryMin/Max` lúc là **VND tuyệt đối**, lúc là **triệu** — không nhất quán thang đo.
- `salaryCurrency` có khi **gắn nhầm**: `'$ 40tr-70tr'` (40–70 triệu VND) bị gắn `USD` + số tuyệt đối
  → nếu ×25.500 ra **nghìn tỷ** (77 job). Có job `'8-15 ₫'` (ý là 8–15 triệu) → nếu đọc thô ra **8 VND** (70 job).
- Có lương ngoài phạm vi: JPY `¥`, SGD `S$` (currency=None).
- Nhưng `prettySalary` LUÔN hiển thị con số theo **triệu VND** (trừ USD thật: có `$`/`USD` và KHÔNG `tr`).

Lương rác (nghìn tỷ / vài VND) sẽ **phá median** ở `/market/metrics` nếu giữ nguyên.

## Quyết định
Chuẩn hoá ở ELT (một chỗ, `normalize_salary`), **lấy CHUỖI RAW làm nguồn đơn vị** (không tin field số):
- có `tr`/`triệu`/`₫` (hoặc không có dấu USD) → con số là **triệu VND** → ×1e6.
- có `$`/`USD` và KHÔNG `tr`/`triệu` → **USD thật** → ×25.500 (`fx-2026-09-v1`, versioned).
- `min` & `max` đều trống (scraper đánh dấu thoả thuận) → **negotiable**, hai cận null.
- một phía (`Từ X`/`Tới X`) → giữ cận hợp lệ, cận kia null (KHÔNG tự suy).
- `min > max`, hoặc cận > **trần sanity 10 tỷ VND/tháng** (`MAX_SALARY_VND_MONTH`) → **invalid**, hai cận null.
  KHÔNG đặt sàn (map-to-triệu đã cứu số bé: `8 ₫`→8 triệu).
- Giữ `salary_*_original`/`raw`/`currency`/`period` kể cả khi invalid (truy vết).

Kết quả dry-run: parsed 7009 (VND 5624 + USD 1385), negotiable 7438, invalid 30 (JPY/SGD + rác magnitude);
76 job `$...tr` về đúng triệu VND; hết outlier nghìn tỷ.

## Phương án đã cân nhắc
- **Tin `currency` + field số của scraper** — sai vì VNW lệch thang đo + gắn nhầm nhãn → ra nghìn tỷ / vài VND.
- **Chỉ đánh invalid khi ngoài dải** (bỏ giá trị) — mất ~147 job; user muốn **phục hồi** về triệu.
- **Hack riêng theo source** (regex `$...tr` cho VNW) — mong manh, rải logic; đọc đơn vị từ raw tổng quát hơn.
- **Sửa schema scraper upstream** — phạm vi lớn, nhiều nền tảng; nhất quán với ADR-019 (xử lý ở ELT).

## Hệ quả
- Tích cực: phục hồi lương gắn-nhầm về đúng triệu VND; loại rác bất khả thi; median đáng tin; FX versioned;
  giữ bản gốc để truy vết; một impl dùng chung fake/duckdb/bq.
- Đánh đổi: heuristic đọc raw có thể sai với định dạng raw lạ tương lai (→ rơi vào invalid, an toàn);
  trần 10 tỷ là quy ước (vài job lương rất cao hợp lệ có thể bị loại — hiếm); multi-period vẫn [SAU].
