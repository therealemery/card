# AGENTS.md

通用授权服务（cardlink-service）：card_key 即客户的交易账号（纯数字 4~32 位，全局唯一主键）。定位与 API 契约细节见 `README.md`，本文件只讲工程约定。

## 项目结构

```
main.py              FastAPI 入口（lifespan：连接初始化 + 启动 APScheduler）
config.py            环境变量读取；DATABASE_URL（SQLite 文件路径）/ MASTER_KEY 无默认值，缺失即拒启
database.py          标准库 sqlite3：threading.local 按线程管连接、WAL + 外键、get_db() 事务上下文（无 ORM）
deps.py              三种身份鉴权：MASTER_KEY / 面板 admin session / Bearer resolve_token
schemas.py           Pydantic 请求模型
scheduler.py         到期扫描 run_expiry_scan() + APScheduler 定时封装 + webhook 推送
routers/
  auth.py            面板管理员登录/登出（账号密码 → admin_sessions 表 Bearer token）
  projects.py        项目自举/列表/删除（X-Master-Key 或面板 session 双通道；列表含 resolve_token；删除级联 cards）
  cards.py           管理面 5 接口（面板 session + ?project_id=）
  resolve.py         查询面唯一接口（仅 resolve_token）
static/
  admin.html         内置单页管理后台（原生 HTML/JS 单文件，禁引 CDN；/admin 直接返回该文件，/ 重定向过去）
migrations/          Alembic（连接串由 env.py 从 config 注入，不写进 alembic.ini）
tests/               pytest；conftest.py 在导入 app 前注入测试环境变量
```

## 常用命令

```bash
source .venv/bin/activate
pip install -r requirements.txt

alembic upgrade head                 # 建表/迁移
uvicorn main:app --port 8000         # 起服务（需先配好 .env）

python -m pytest tests/ -v           # 测试（每会话独立临时 SQLite 文件，无需外部数据库）
```

## 约定

- 代码注释、文档、commit 统一简体中文。
- SQL 用标准库 `sqlite3` 手写，占位符用 `?`，不引 ORM；schema 变更一律走 Alembic 迁移，不在代码里建表。
- 时间字段一律存 UTC ISO8601 字符串（`database.utcnow_iso()`）；时间计算在 Python 层做（续费 GREATEST、临期 deadline），SQL 里只做同格式字符串比较。
- 敏感配置只从环境变量读，代码里不得出现默认值密钥；`.env`、`data/`、`*.db` 不进仓库（`.env.example` 同步更新）。
- 面板（/admin）与 API 密钥是两套凭证：面板走 admin session（账号密码登录，可管理所有项目，调 cards 必带 `?project_id=`）；X-Master-Key / resolve_token 保留给服务器间调用，语义不要动。项目级管理密钥（admin_key）已在 0003 迁移中废弃删除，不要再引入。
- 核心业务语义（改动前先读相关代码与测试）：
  - card_key = 交易账号，调用方传入（纯数字 4~32 位，Pydantic 校验），服务不生成；全局唯一，重复创建返回 409。
  - 过期不是状态，由 `expires_at` 表达；状态机仅 `active/suspended/revoked`。
  - 续费 = `GREATEST(expires_at, NOW()) + days`。
  - reset = 任意状态（含 revoked）→ active，不动 `expires_at`（误吊销恢复用）。
  - webhook 去重靠 `reminded_at` / `expired_notified_at`，只在推送成功后标记。
- 每个接口都必须校验项目归属（`project_id`），跨项目一律 404/视不存在（card_key 唯一性除外，见上）。
- 改 API 契约时同步更新 `README.md` 的契约表与测试。
