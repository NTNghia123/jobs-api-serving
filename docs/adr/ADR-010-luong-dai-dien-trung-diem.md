# ADR-010: "Lương đại diện" = trung điểm khoảng lương; benchmark bằng median

- Trạng thái: Đã chấp nhận
- Ngày: [Điền ngày — tuần 5]
- Người quyết định: [Điền tên]

## Bối cảnh
Mỗi tin có một khoảng lương `[minimumsalary, maximumsalary]` (đã CAST sang số ở Tuần 3). Để benchmark
theo nhóm (cấp bậc / quốc gia) cần quy mỗi tin về **một** con số, rồi tổng hợp cả nhóm. Cần chốt: dùng
con số nào cho mỗi tin, và dùng phép tổng hợp nào cho nhóm.

## Quyết định
- **Lương đại diện của một tin = trung điểm** `(salary_min + salary_max) / 2`.
- **Chỉ số nhóm = median** của các lương đại diện trong nhóm (kèm `posting_count`).
Logic này sống ở **bước transform** (job nướng gold `build_gold.py`), không rải trong API.

## Phương án đã cân nhắc
- **Dùng `salary_min` hoặc `salary_max` đơn lẻ** — thiên lệch (thấp/cao) một phía. Trung điểm cân bằng
  hơn. Loại.
- **Tổng hợp bằng mean** — nhạy với ngoại lệ (một tin lương rất cao kéo lệch). **Median** ổn định hơn
  với mẫu nhỏ và phân bố lệch. Chọn median.
- **Median của min và median của max riêng** — trả 2 số, khó dùng cho benchmark một điểm. Loại; giữ
  một con số đại diện cho dễ tiêu thụ.

## Hệ quả
- Tích cực: định nghĩa nhất quán một chỗ (semantic layer); consumer nhận một con số dễ hiểu kèm số tin.
- Đánh đổi: trung điểm **giả định** phân bố đều trong khoảng — không hẳn đúng thực tế, nhưng là xấp xỉ
  hợp lý khi nguồn chỉ cho biết khoảng. **Đơn vị lương KHÔNG xác định** trong nguồn (số nguyên thô) →
  giữ nguyên giá trị, ghi chú "đơn vị không xác định" trong tài liệu, KHÔNG bịa "VND".
- Nhóm quá nhỏ bị che bởi k-anonymity ở tầng API (median_salary = null) — xem QueryValidator.
