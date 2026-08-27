# ADR-010: "Lương đại diện" = trung điểm khoảng lương; benchmark bằng median

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 5]
- Người quyết định: [Điền tên]

## Bối cảnh
Mỗi tin có khoảng lương `[minimumsalary, maximumsalary]` (đã CAST sang số ở Tuần 3). Để benchmark theo
nhóm (cấp bậc / quốc gia) cần quy mỗi tin về một con số, rồi tổng hợp cả nhóm.

## Quyết định
- **Lương đại diện của một tin = trung điểm** `(salary_min + salary_max) / 2`.
- **Chỉ số nhóm = median** của các lương đại diện (kèm `posting_count`).
Logic sống ở **bước transform** (`app/elt/build_gold.py`), không rải trong API.

## Phương án đã cân nhắc
- **Dùng `salary_min`/`salary_max` đơn lẻ** — thiên lệch một phía. Trung điểm cân bằng hơn. Loại.
- **Tổng hợp bằng mean** — nhạy ngoại lệ; **median** ổn định hơn với mẫu nhỏ, lệch. Chọn median.
- **Median của min và của max riêng** — trả 2 số, khó dùng cho benchmark một điểm. Loại.

## Hệ quả
- Tích cực: định nghĩa nhất quán một chỗ; consumer nhận một con số dễ hiểu kèm số tin.
- Đánh đổi: trung điểm giả định phân bố đều trong khoảng — xấp xỉ hợp lý khi nguồn chỉ cho biết khoảng.
  **Đơn vị lương KHÔNG xác định** trong nguồn (số nguyên thô) → giữ nguyên, không bịa "VND".
- Nhóm quá nhỏ bị che bởi k-anonymity ở tầng API (`median_salary = null`).
