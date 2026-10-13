# FreshMeal AI Planner API

FastAPI MVP backend. The API uses Supabase Auth JWTs and PostgreSQL; Supabase
owns login, while the mobile application calls the versioned API routes.

## Setup

1. Create/activate the existing project virtual environment and install:

   ```powershell
   pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env`, then configure the existing Supabase project's
   PostgreSQL `DATABASE_URL`, `SUPABASE_URL`, and `GEMINI_API_KEY`. Never commit `.env`.
   Copy the Session pooler URI from the intended Supabase project's **Connect**
   dialog and URL-encode special characters in its password.
   PostgreSQL URLs in `postgres://` or `postgresql://` form are normalized to
   the installed psycopg driver.
3. Apply migrations and seed data to the intended database:

   ```powershell
   python -m scripts.run_migrations
   ```

   These operations are explicit; application startup never migrates the
   database. `scripts.seed_data` is mock-only and requires `IS_MOCKING=true`;
   never use it for the real app. Migration `002_app_data.sql` adds the mobile
   snapshot, replay keys, and user-owned AI recipes. Review SQL before applying.
4. Start the API:

   ```powershell
   uvicorn app.main:app --reload
   ```

   Interactive OpenAPI is available at `/docs`; the internal health endpoint
   is `/health`.

The mobile app calls the versioned routes in `app/api/routes/`: snapshot sync
under `users`, AI recipes under `recipes`, plan/shopping under `meal_plans`, and
image detection under `scan_jobs`. Confirmed inventory drives recipe generation;
the backend does not seed a fixed catalog in live mode. The app's
`EXPO_PUBLIC_IS_MOCKING` and backend `IS_MOCKING` must both be `true` to use
their respective mock data. Native devices need a reachable HTTPS or LAN API
origin rather than `127.0.0.1`.

## Xuất hợp đồng OpenAPI

Từ `../fresh-plan`, `npm run generate:api` thực hiện kiểm tra project/database live,
kiểm tra drift rồi xuất OpenAPI qua stdout và sinh API types đang dùng ở frontend.
Cài package codegen riêng bằng `npm run setup:schemas`; xem
`../fresh-plan/SCHEMA_CODEGEN.md` về đăng nhập CLI, baseline và giới hạn drift.
`scripts.schema_contract database` chỉ đọc catalog trong transaction READ ONLY; không chạy
migration. `database.schema.json` là baseline remote, không phải lịch sử migration.

Chạy từ thư mục `fast-api` với môi trường Python đã cài dependencies:

```powershell
python -m scripts.schema_contract api
```

Lệnh xuất JSON qua stdout từ route và model Pydantic hiện tại. Không cần `.env`,
credentials, database hay server đang chạy. CLI dùng cấu hình SQLite trong bộ nhớ riêng cho tiến trình xuất, không mở kết nối và không chạy lifespan, migration hoặc AI.
File JSON có thứ tự key ổn định, UTF-8 và newline LF; không sửa thủ công.
Có thể xuất file để xem bằng `--output <path>` (thư mục đích phải tồn tại), nhưng
không lưu thêm OpenAPI JSON vào repo. Khi đổi hợp đồng, sinh lại và review diff
`fresh-plan/src/services/generated/api.types.ts` cùng source.
Các field bên trong JSONB lấy từ Pydantic; thêm cột PostgreSQL không tự đổi OpenAPI.

OpenAPI mô tả envelope lỗi `{error: {code, message}, detail: {code, message}}`,
header bắt buộc `If-Match`/`Idempotency-Key` của PUT snapshot, các lỗi revision,
idempotency, upload và AI. Một số route không được mobile dùng vẫn giữ response
dictionary tổng quát; bước này không mở rộng model cho các route đó.

Các script còn lại có mục đích riêng: `run_migrations` áp dụng SQL khi được yêu cầu;
`seed_data` chỉ nạp dữ liệu mock. Chúng không thuộc codegen và không chạy tự động.
Các database types Python/TypeScript không có nơi dùng đã bỏ; Pydantic gắn vào
`response_model` của route vẫn thực hiện validation/serialization tại runtime.

## Tests

Kiểm thử xuất OpenAPI và hợp đồng mobile không cần PostgreSQL:

```powershell
$env:DATABASE_URL = 'sqlite+pysqlite:///:memory:'
python -m unittest tests.test_openapi_export tests.test_mobile_contract -v
python -m unittest tests.test_recipe_generation -v
```

Bộ export chạy trong tiến trình con, chặn kết nối DB/mạng và kiểm tra sinh file
hai lần cho cùng nội dung. Bộ mobile dùng DB/AI mock, không gọi dịch vụ thật.

The AI layer has a standalone offline suite that needs no database:

```powershell
python -m pytest --confcutdir=tests/ai tests/ai -q
```

Tests require a dedicated local PostgreSQL database. They reject non-local
hosts, create a minimal `auth.users` stub if needed, and run migrations/seed
only against `TEST_DATABASE_URL`. They use generated RSA JWT keys and fake AI
clients; they do not call Supabase or Gemini.

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://postgres:<local-password>@localhost:5432/freshplandb'; $env:DATABASE_URL = $env:TEST_DATABASE_URL; python -m pytest -q
```

## Scan storage and operational notes

- Uploads accept JPEG, PNG, or WebP, are limited to 5 MB, checked against magic
  bytes and decoded before a job is created.
- The MVP uses FastAPI `BackgroundTasks` and private local files under
  `var/scans/`; processing concurrency and stuck-job recovery are configured
  through `.env`. This is single-process storage, not durable queue/object
  storage. Use shared private object storage and a durable worker before
  deploying multiple API instances.
- Job images are removed after confirmation or expiration. The scan worker
  stores only normalized public error codes, not provider response bodies.
- The AI scan source is vendored under `app/ai/vendor/`; its key-file loader is
  not used. The backend reads `GEMINI_API_KEY`; image scans use
  `GEMINI_SCAN_MODEL` (default `gemini-2.5-pro`) and
  `GEMINI_SCAN_TIMEOUT_SECONDS` (default 90 seconds). Other AI tasks use
  `GEMINI_MODEL` and `GEMINI_TIMEOUT_SECONDS`. Restart the backend after
  changing these settings. Pro requests can take longer and cost more;
  availability and quotas depend on the Google API project.
- Gemini scan requests include a JSON schema capped at 20 ingredients. The
  mobile detect route preserves rate-limit (429), timeout (504), invalid-output
  (502), and configuration/provider-unavailable (503) error codes. Offline
  tests in `tests/ai/test_scan_flow.py` cover this route without database access.
- `sql/migrations/001_user_scan_idempotency.sql` adds foreign keys as `NOT
  VALID` to avoid blocking deployment on legacy orphan rows. New writes are
  checked; validate those constraints separately after reviewing legacy data.

## Lớp AI từ GeminiAgentMealPLanner

Logic `agent.py` và `recipe.py` được tích hợp vào `app/ai/vendor/`, cùng adapter, key router và scan hiện có. Backend chạy độc lập, không import thư mục service gốc và không đọc `.env.ai`. Không cần dependency hoặc migration mới.

Điểm gọi dùng cấu hình FastAPI:

```python
from app.ai.gemini_provider import generate_recipe, run_agent
from app.ai.vendor.agent import ChatMessage

result = run_agent(
    user_message="Tôi còn gì trong kho?",
    history=[ChatMessage(role="user", content="Tôi muốn nấu bữa tối")],
    tool_dispatcher=backend_dispatcher,
    auth_context={"user_id": authenticated_user_id},
    max_tool_calls=5,
)

recipe_text = generate_recipe(
    recipe="Luộc 2 quả trứng trong 5 phút.",
    user_context={"preferences": ["Hướng dẫn ngắn gọn"]},
)
```

`backend_dispatcher` là callback do backend cung cấp, theo `ToolDispatcher` trong
`vendor/agent.py`: `execute(tool_name=..., payload=..., auth_context=...)` trả
`ToolExecutionResult`. Callback phải xác thực user, kiểm tra quyền sở hữu,
validate dữ liệu nghiệp vụ và ghi dữ liệu an toàn; không lấy user ID từ tham số
do model sinh. Lớp AI chỉ kiểm tra hợp đồng tool, không tự có quyền truy cập DB.
Chưa thêm endpoint chat hoặc dispatcher ghi dữ liệu trong lần tích hợp này.

Agent chỉ gọi `view`, `add`, `update`, `delete` trên `user_context`, `inventory`,
`shopping_list`. Mỗi lượt sửa tool call sai tối đa một lần, không chạy batch
vượt giới hạn và dừng khi callback trả lỗi. Khi dùng hết budget, model vẫn được
trả lời kết thúc, nhưng không được gọi thêm tool. Kết quả có `status`, `message`,
`steps_used`, `tool_results`, `error`; các thao tác đã thành công trước lỗi vẫn
nằm trong `tool_results`, không tự rollback toàn bộ lượt hội thoại.

Adapter dùng `systemInstruction`, `functionCall` và `functionResponse` có cấu
trúc, giữ nguyên các part/thought signature của model. Lỗi transport được chuyển
thành `APIError` công khai mà không trả response hoặc key của Gemini.

`generate_recipe` chỉ diễn đạt lại công thức nguồn bằng plain text. Prompt yêu
cầu giữ nguyên nguyên liệu, lượng, đơn vị, thứ tự, thời gian và nhiệt độ; đây là
yêu cầu gửi model, không phải kiểm chứng tự động tính tương đương của văn bản.
Luồng `services/recipe_generation.py` sinh recipe JSON từ inventory và luồng
scan vẫn dùng các hợp đồng hiện có.
