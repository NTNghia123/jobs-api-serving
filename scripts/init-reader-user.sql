-- Tự động chạy khi container MySQL khởi tạo LẦN ĐẦU (ổ dữ liệu rỗng), cùng cơ chế với database.sql.
-- Tên file bắt đầu bằng 'i' nên chạy SAU 'database.sql' (thứ tự alphabet) — dù CREATE USER/GRANT
-- không thực sự cần bảng đã tồn tại trước, giữ thứ tự này cho rõ ràng.
--
-- Mục đích: user CHỈ-ĐỌC cho job ELT (app/elt/build_silver.py). Không dùng root để job ELT
-- không có khả năng ghi/sửa/xoá dữ liệu nguồn, kể cả khi code có bug.

CREATE USER 'reader'@'%' IDENTIFIED BY 'reader_pw';
GRANT SELECT ON jobportal.* TO 'reader'@'%';
FLUSH PRIVILEGES;
