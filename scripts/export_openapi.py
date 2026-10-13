import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    # Console Windows có thể dùng cp1252; giữ thông báo và --help ở UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[1] / "openapi.json",
        help="Đường dẫn file JSON được sinh (mặc định: fast-api/openapi.json).",
    )
    args = parser.parse_args()

    # Chỉ ghi đè môi trường của tiến trình CLI này, không sửa file .env.
    # Engine SQLite chỉ được tạo; app.openapi() không mở kết nối hay chạy lifespan.
    os.environ.update({
        "DATABASE_URL": "sqlite+pysqlite:///:memory:",
        "SUPABASE_URL": "",
        "SUPABASE_JWT_SECRET": "",
        "GEMINI_API_KEY": "",
        "IS_MOCKING": "false",
        "WEB_ORIGINS": "",
    })
    from app.main import app

    document = json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    args.output.write_text(document, encoding="utf-8", newline="\n")
    print(f"Đã xuất OpenAPI: {args.output}")


if __name__ == "__main__":
    main()
