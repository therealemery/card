# cardlink-service

通用授权服务：客户的标识是一串数字的**交易账号**，"卡号"就是交易账号本身。接入方（项目）为账号创建授权并设期限，软件运行时调 `resolve` 校验授权，到期前服务自动 webhook 提醒项目方。

定位刻意收窄：只做"授权 → 校验 → 到期提醒"这一件事。不做 PC 客户端、不做 AES/HMAC、不管任务调度。

- 技术栈：FastAPI + PostgreSQL（psycopg2 直连，无 ORM）+ Alembic + APScheduler + pytest/httpx
- 两张表：`projects`（接入方）/ `cards`（账号授权，card_key = 交易账号）
- 过期不是状态，由 `expires_at` 表达；状态机仅 `active / suspended / revoked`

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

# 2. 项目方用自己的 admin_key 给客户交易账号 88801234 创建 30 天授权
curl -s -X POST http://127.0.0.1:8000/api/cards \
  -H "X-Admin-Key: <admin_key>" -H "Content-Type: application/json" \
  -d '{"card_key": "88801234", "days": 30, "plan_code": "pro", "remark": "某某客户"}'
# → {"card":{"card_key":"88801234", "status":"active", "expires_at":"...", ...}}

# 3. 软件运行时校验（项目方后端用 resolve_token 调用）
curl -s -X POST http://127.0.0.1:8000/api/resolve \
  -H "Authorization: Bearer <resolve_token>" -H "Content-Type: application/json" \
  -d '{"card_key": "88801234"}'
# → {"valid":true, "reason":null, "status":"active", "plan_code":"pro", "expires_at":"..."}

# 4. 续费 30 天（GREATEST 语义：未过期从原到期顺延，已过期从现在起算）
curl -s -X POST http://127.0.0.1:8000/api/cards/88801234/renew \
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
| 项目（管理面） | `X-Admin-Key: <项目 admin_key>` | 创建授权/续费/状态管理 |
| 项目（查询面） | `Authorization: Bearer <项目 resolve_token>` | resolve 校验 |

| 方法 & 路径 | 鉴权 | 说明 | 请求体 / 参数 | 响应要点 |
|---|---|---|---|---|
| `POST /api/projects` | Master | 创建项目 | `{name, callback_url?}` | 返回 `admin_key` / `resolve_token`（仅此一次） |
| `GET /api/projects` | Master | 项目列表 | — | 不回传密钥 |
| `POST /api/cards` | Admin | 创建授权 | `{card_key, days≥1, plan_code?, remark?}`，card_key 为纯数字 4~32 位交易账号 | 账号已存在返回 409 |
| `GET /api/cards` | Admin | 列表 | `?status=active\|suspended\|revoked`、`?expiring_in=7d`（N 天内到期且未过期） | `{cards, count}` |
| `GET /api/cards/{key}` | Admin | 详情 | — | 404 不存在或跨项目 |
| `POST /api/cards/{key}/renew` | Admin | 续费 | `{days≥1}` | `expires_at = GREATEST(expires_at, NOW()) + days` |
| `PATCH /api/cards/{key}` | Admin | 状态变更 / 改备注 | `{action?: suspend\|resume\|revoke\|reset, remark?}`（至少给一项；只给 remark 则不变状态） | 非法流转返回 409 |
| `POST /api/resolve` | Bearer | 校验账号授权 | `{card_key}` | 见下 |

状态机：`suspend` 仅 `active→suspended`；`resume` 仅 `suspended→active`；`revoke` 任意态→`revoked`；`reset` 任意态（含 revoked）→`active`，不动 `expires_at`（误吊销恢复用，期限靠续费调）。

`POST /api/resolve` 响应：

```json
{ "valid": true,  "reason": null, "card_key": "88801234", "status": "active", "plan_code": "pro", "expires_at": "..." }
```

`valid=false` 时 `reason` 取值：`not_found`（不存在或属其他项目）/ `suspended` / `revoked` / `expired`（此时 `status` 仍是 `active`，过期由 `expires_at` 表达）。

## 管理后台

服务自带一个零依赖的单页管理后台：起服务后浏览器访问 **`/admin`**（`/` 会自动重定向过去）。页面是 `static/admin.html` 单文件，原生 HTML/CSS/JS，不引任何 CDN，内网环境可直接用。

登录：在页面顶部粘贴密钥，页面自动识别身份（先试 `GET /api/cards`，403 再试 `GET /api/projects`）。密钥存浏览器 localStorage，点"退出"清除。

- **项目管理密钥（X-Admin-Key）** → 授权管理视图
- **MASTER_KEY** → 项目管理视图

功能清单：

- 授权管理：添加客户授权（交易账号 + 授权天数 + 备注，账号纯数字校验）；列表按账号搜索、按状态 / `expiring_in` 筛选；到期时间 7 天内橙色、已过期红色高亮；续费（输入天数）；暂停/恢复；吊销（二次确认）；重置（suspended/revoked 行显示，二次确认，恢复 active 不动到期时间）；点击备注直接修改；账号一键复制
- 项目管理：创建项目（name + callback_url），创建后 `admin_key` / `resolve_token` 醒目展示并提示只显示一次，附一键复制；项目列表

## Python 软件接入示例

以前到期时间硬编码在客户代码里：

```python
# 以前：改一次到期时间就要重新发版，客户改系统时间就能绕过
EXPIRE_DATE = "2026-12-31"
if datetime.now() > datetime.fromisoformat(EXPIRE_DATE):
    sys.exit("软件已到期")
```

现在改成调 cardlink-service 校验交易账号：

```python
# check_license.py —— 可直接复制（依赖：pip install requests）
import json
import os
import time

import requests

# 自己服务器上包的一层校验接口（见下文"部署要点"），不是 cardlink-service 本体
LICENSE_URL = os.environ.get("LICENSE_URL", "https://your-server.example.com/license/check")
CACHE_FILE = os.path.expanduser("~/.yourapp_license_cache.json")
GRACE_SECONDS = 24 * 3600  # 服务连不上时，缓存宽限 24 小时


def check_license(account: str) -> tuple[bool, str | None]:
    """
    校验交易账号授权。返回 (是否有效, 到期时间 ISO 字符串或 None)。
    网络失败时回退到本地缓存（宽限 GRACE_SECONDS），缓存也没有则拒绝（fail-closed）。
    """
    try:
        resp = requests.post(LICENSE_URL, json={"account": account}, timeout=5)
        data = resp.json()
        if resp.status_code == 200 and data.get("valid"):
            # 校验成功：写缓存，供服务不可用时宽限
            with open(CACHE_FILE, "w") as f:
                json.dump({"account": account, "expires_at": data["expires_at"],
                           "checked_at": time.time()}, f)
            return True, data["expires_at"]
        return False, None  # 明确无效（吊销/暂停/过期/不存在）：立即拒绝，不走缓存
    except requests.RequestException:
        # 连不上服务：走缓存宽限（fail-open 的收敛版）
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            fresh = time.time() - cache["checked_at"] < GRACE_SECONDS
            if cache["account"] == account and fresh:
                return True, cache["expires_at"]
        except Exception:
            pass
        return False, None  # 无缓存或缓存过老：拒绝


# 用法：软件启动时校验一次（fail-closed），运行中可定时再校验
if __name__ == "__main__":
    ok, expires_at = check_license(input("请输入交易账号: ").strip())
    if not ok:
        raise SystemExit("授权无效或已过期，请联系供应商")
    print(f"授权有效，到期时间：{expires_at}")
```

对比硬编码：到期时间集中在服务端管理，续费/停用即时生效，不用重新发版；客户改本机时间不影响服务端的 `expires_at`。

### 部署要点

- `resolve_token` **只能放在自己服务器的环境变量里，绝不能下发到客户机器**。客户机器上的任何字符串都能被抠出来，一旦泄露别人就能随便查你的授权库。
- 软件跑在客户机器上时，在自己服务器上包一层校验接口，客户软件只调这层包装。最小示例（FastAPI）：

```python
# 你自己服务器上的包装接口：对客户只暴露 account → valid/expires_at
import os
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

CARDLINK_URL = "http://127.0.0.1:8000/api/resolve"   # 内网地址即可
RESOLVE_TOKEN = os.environ["CARDLINK_RESOLVE_TOKEN"]  # 环境变量注入，不写进代码

app = FastAPI()

class CheckReq(BaseModel):
    account: str

@app.post("/license/check")
def check(req: CheckReq):
    r = httpx.post(CARDLINK_URL, json={"card_key": req.account},
                   headers={"Authorization": f"Bearer {RESOLVE_TOKEN}"}, timeout=5)
    data = r.json()
    # 只回传客户需要的最小信息，reason/status 等内部细节不下发
    return {"valid": data["valid"], "expires_at": data.get("expires_at")}
```

### 连不上卡号服务时的兜底策略

两种极端都不合适：完全 fail-open（连不上就放行）等于没校验；完全 fail-closed（连不上就拒绝）会让服务抖动误伤正常客户。上面的示例采用折中：

- **启动时 fail-closed**：首次校验必须成功（或命中有效缓存），否则拒绝运行；
- **运行中带宽限缓存**：每次校验成功把结果写本地缓存，服务不可用时用缓存放行，但缓存超过 24 小时（`GRACE_SECONDS`）未刷新就转为拒绝——客户长期断网绕过校验的窗口被封死；
- 注意示例里**明确无效（valid=false）不走缓存**，吊销/停用是即时生效的。

### 运营动作速查

| 场景 | 操作 | 接口 / 面板按钮 |
|------|------|----------------|
| 新客户开通 | 创建授权 | `POST /api/cards`（面板"添加客户授权"） |
| 续费 | renew（未过期顺延，已过期从今天起算） | `POST /api/cards/{账号}/renew` |
| 临时停用（如欠费） | suspend | `PATCH` `{"action":"suspend"}` |
| 恢复停用 | resume | `PATCH` `{"action":"resume"}` |
| 彻底停用 | revoke | `PATCH` `{"action":"revoke"}` |
| 误吊销/误操作恢复 | reset（回 active，到期时间不变） | `PATCH` `{"action":"reset"}` |

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
  "card_key": "88801234", "plan_code": "pro", "expires_at": "...", "project_id": 1 }
```

标记列只在推送成功（2xx）后写入；失败仅记日志，下轮扫描自动重试。项目无 `callback_url` 或授权已 `revoked` 不推送。

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DATABASE_URL` | 是 | 无（缺失拒启） | PostgreSQL 连接串 |
| `MASTER_KEY` | 是 | 无（缺失拒启） | 项目自举接口主密钥 |
| `SCAN_INTERVAL_MINUTES` | 否 | `60` | 到期扫描间隔（分钟） |
| `EXPIRING_DAYS` | 否 | `7` | 临期阈值（天） |
