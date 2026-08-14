# cardlink-service

通用卡号授权服务：接入方（项目）发卡设期限，运行时调 `resolve` 校验授权，到期前服务自动 webhook 提醒项目方。

定位刻意收窄：只做"发卡 → 校验 → 到期提醒"这一件事。不做 PC 客户端、不做 AES/HMAC、不管任务调度。

- 技术栈：FastAPI + PostgreSQL（psycopg2 直连，无 ORM）+ Alembic + APScheduler + pytest/httpx
- 两张表：`projects`（接入方）/ `cards`（卡号）
- 过期不是状态，由 `expires_at` 表达；卡状态机仅 `active / suspended / revoked`

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 填入 DATABASE_URL 和 MASTER_KEY（两者均无默认值，缺失拒启）

alembic upgrade head   # 建表

uvicorn main:app --port 8000
```

一条龙示例（假设 MASTER_KEY=master-secret）：

```bash
# 1. 创建项目（服务运营方操作；admin_key / resolve_token 只在创建时返回一次）
curl -s -X POST http://127.0.0.1:8000/api/projects \
  -H "X-Master-Key: master-secret" -H "Content-Type: application/json" \
  -d '{"name": "my-app", "callback_url": "https://my-app.example.com/cardlink/webhook"}'
# → {"id":1, "admin_key":"...", "resolve_token":"...", ...}

# 2. 项目方用自己的 admin_key 发 3 张 30 天卡
curl -s -X POST http://127.0.0.1:8000/api/cards \
  -H "X-Admin-Key: <admin_key>" -H "Content-Type: application/json" \
  -d '{"count": 3, "days": 30, "plan_code": "pro", "remark": "首发批次"}'
# → {"cards":[{"card_key":"AB12-CD34-EF56-GH78", ...}], "count":3}

# 3. 运行时校验（项目方后端用 resolve_token 调用）
curl -s -X POST http://127.0.0.1:8000/api/resolve \
  -H "Authorization: Bearer <resolve_token>" -H "Content-Type: application/json" \
  -d '{"card_key": "AB12-CD34-EF56-GH78"}'
# → {"valid":true, "reason":null, "status":"active", "plan_code":"pro", "expires_at":"..."}

# 4. 续费 30 天（GREATEST 语义：未过期从原到期顺延，已过期从现在起算）
curl -s -X POST http://127.0.0.1:8000/api/cards/AB12-CD34-EF56-GH78/renew \
  -H "X-Admin-Key: <admin_key>" -H "Content-Type: application/json" -d '{"days": 30}'
```

跑测试（需要本地 PostgreSQL，连接串见 `tests/conftest.py` 顶部注释，默认 `127.0.0.1:15432/cardlink_test`）：

```bash
python -m pytest tests/ -v
```

## API 契约

三种身份：

| 身份 | 凭证 | 用途 |
|------|------|------|
| 服务运营方 | `X-Master-Key: <MASTER_KEY 环境变量>` | 项目自举 |
| 项目（管理面） | `X-Admin-Key: <项目 admin_key>` | 发卡/续费/状态/换卡 |
| 项目（查询面） | `Authorization: Bearer <项目 resolve_token>` | resolve 校验 |

| 方法 & 路径 | 鉴权 | 说明 | 请求体 / 参数 | 响应要点 |
|---|---|---|---|---|
| `POST /api/projects` | Master | 创建项目 | `{name, callback_url?}` | 返回 `admin_key` / `resolve_token`（仅此一次） |
| `GET /api/projects` | Master | 项目列表 | — | 不回传密钥 |
| `POST /api/cards` | Admin | 批量生成 | `{count=1..100, days≥1, plan_code, remark}` | 卡号格式 `XXXX-XXXX-XXXX-XXXX` |
| `GET /api/cards` | Admin | 列表 | `?status=active\|suspended\|revoked`、`?expiring_in=7d`（N 天内到期且未过期） | `{cards, count}` |
| `GET /api/cards/{key}` | Admin | 详情 | — | 404 不存在或跨项目 |
| `POST /api/cards/{key}/renew` | Admin | 续费 | `{days≥1}` | `expires_at = GREATEST(expires_at, NOW()) + days` |
| `PATCH /api/cards/{key}` | Admin | 状态变更 | `{action: suspend\|resume\|revoke, remark?}` | 非法流转返回 409 |
| `POST /api/cards/{key}/replace` | Admin | 换卡 | — | 老卡 revoked，新卡继承剩余有效期，`renewed_from` 溯源 |
| `POST /api/resolve` | Bearer | 校验卡号 | `{card_key}` | 见下 |

状态机：`suspend` 仅 `active→suspended`；`resume` 仅 `suspended→active`；`revoke` 任意态→`revoked`（终态）。

`POST /api/resolve` 响应：

```json
{ "valid": true,  "reason": null, "card_key": "...", "status": "active", "plan_code": "pro", "expires_at": "..." }
```

`valid=false` 时 `reason` 取值：`not_found`（不存在或属其他项目）/ `suspended` / `revoked` / `expired`（此时 `status` 仍是 `active`，过期由 `expires_at` 表达）。

## Webhook 到期提醒

进程内 APScheduler 每 `SCAN_INTERVAL_MINUTES` 分钟扫一次（默认 60），向项目的 `callback_url` POST：

- 剩余 ≤ `EXPIRING_DAYS` 天（默认 7）且未提醒过 → `card.expiring`（`reminded_at` 去重）
- 已过期且未通知过 → `card.expired`（`expired_notified_at` 去重）

请求格式：

```
POST <callback_url>
Authorization: Bearer <项目 resolve_token>
Content-Type: application/json

{ "event": "card.expiring" | "card.expired",
  "card_key": "...", "plan_code": "...", "expires_at": "...", "project_id": 1 }
```

标记列只在推送成功（2xx）后写入；失败仅记日志，下轮扫描自动重试。项目无 `callback_url` 或卡已 `revoked` 不推送。

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DATABASE_URL` | 是 | 无（缺失拒启） | PostgreSQL 连接串 |
| `MASTER_KEY` | 是 | 无（缺失拒启） | 项目自举接口主密钥 |
| `SCAN_INTERVAL_MINUTES` | 否 | `60` | 到期扫描间隔（分钟） |
| `EXPIRING_DAYS` | 否 | `7` | 临期阈值（天） |
