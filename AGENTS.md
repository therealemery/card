# AGENTS.md

通用卡号授权服务（cardlink-service）。定位与 API 契约细节见 `README.md`，本文件只讲工程约定。

## 项目结构

```
main.py              FastAPI 入口（lifespan：连接池 + 启动 APScheduler）
config.py            环境变量读取；DATABASE_URL / MASTER_KEY 无默认值，缺失即拒启
database.py          psycopg2 连接池 + get_db() 事务上下文管理器（无 ORM）
deps.py              三种身份鉴权：MASTER_KEY / X-Admin-Key / Bearer resolve_token
schemas.py           Pydantic 请求模型
scheduler.py         到期扫描 run_expiry_scan() + APScheduler 定时封装 + webhook 推送
routers/
  projects.py        项目自举（MASTER_KEY 保护）
  cards.py           管理面 6 接口（生成/列表/详情/续费/状态/换卡）
  resolve.py         查询面唯一接口
migrations/          Alembic（连接串由 env.py 从 config 注入，不写进 alembic.ini）
tests/               pytest；conftest.py 在导入 app 前注入测试环境变量
```

## 常用命令

```bash
source .venv/bin/activate
pip install -r requirements.txt

alembic upgrade head                 # 建表/迁移
uvicorn main:app --port 8000         # 起服务（需先配好 .env）

python -m pytest tests/ -v           # 测试（需本地 PG，默认 127.0.0.1:15432/cardlink_test）
```

## 约定

- 代码注释、文档、commit 统一简体中文。
- SQL 用 psycopg2 手写，不引 ORM；schema 变更一律走 Alembic 迁移，不在代码里建表。
- 敏感配置只从环境变量读，代码里不得出现默认值密钥；`.env` 不进仓库（`.env.example` 同步更新）。
- 核心业务语义（改动前先读相关代码与测试）：
  - 过期不是状态，由 `expires_at` 表达；状态机仅 `active/suspended/revoked`。
  - 续费 = `GREATEST(expires_at, NOW()) + days`。
  - 换卡 = 老卡 revoked + 新卡继承老卡 `expires_at`，`renewed_from` 溯源。
  - webhook 去重靠 `reminded_at` / `expired_notified_at`，只在推送成功后标记。
- 每个接口都必须校验项目归属（`project_id`），跨项目一律 404/视不存在。
- 改 API 契约时同步更新 `README.md` 的契约表与测试。
