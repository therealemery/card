# cardlink-service

通用授权服务：客户的标识是一串数字的**交易账号**，"卡号"就是交易账号本身。接入方（项目）为账号创建授权并设期限，软件运行时调 `resolve` 校验授权，到期前服务自动 webhook 提醒项目方。

定位刻意收窄：只做"授权 → 校验 → 到期提醒"这一件事。不做 PC 客户端、不做 AES/HMAC、不管任务调度。

- 技术栈：FastAPI + SQLite（标准库 `sqlite3`，WAL 模式，无 ORM）+ Alembic + APScheduler + pytest/httpx
- 两张表：`projects`（接入方）/ `cards`（账号授权，card_key = 交易账号）
- 过期不是状态，由 `expires_at` 表达；状态机仅 `active / suspended / revoked`
- 时间字段统一存 UTC ISO8601 字符串，同格式下字符串比较即时间比较

## 快速开始

无需安装任何数据库服务，SQLite 就是一个文件：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 填入 DATABASE_URL（SQLite 文件路径）和 MASTER_KEY（均无默认值，缺失拒启）

alembic upgrade head   # 建表（数据文件所在目录会自动创建）

uvicorn main:app --port 8000
```

一条龙示例（假设 MASTER_KEY=master-secret，面板账号 admin）：

```bash
# 1. 创建项目（服务运营方操作；响应含 resolve_token，列表接口可随时再查）
curl -s -X POST http://127.0.0.1:8000/api/projects \
  -H "X-Master-Key: master-secret" -H "Content-Type: application/json" \
  -d '{"name": "my-app", "callback_url": "https://my-app.example.com/cardlink/webhook"}'
# → {"id":1, "resolve_token":"...", ...}

# 2. 管理员登录拿 session（发卡/续费等管理操作都走面板会话）
curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" -d '{"username": "admin", "password": "<ADMIN_PASSWORD>"}'
# → {"token":"<session>", "expires_at":"..."}

# 3. 给客户交易账号 88801234 创建 30 天授权（session + ?project_id=）
curl -s -X POST "http://127.0.0.1:8000/api/cards?project_id=1" \
  -H "Authorization: Bearer <session>" -H "Content-Type: application/json" \
  -d '{"card_key": "88801234", "days": 30, "remark": "某某客户"}'
# → {"card":{"card_key":"88801234", "status":"active", "expires_at":"...", ...}}

# 4. 软件运行时校验（项目方后端用 resolve_token 调用）
curl -s -X POST http://127.0.0.1:8000/api/resolve \
  -H "Authorization: Bearer <resolve_token>" -H "Content-Type: application/json" \
  -d '{"card_key": "88801234"}'
# → {"valid":true, "reason":null, "status":"active", "expires_at":"..."}

# 5. 续费 30 天（GREATEST 语义：未过期从原到期顺延，已过期从现在起算）
curl -s -X POST "http://127.0.0.1:8000/api/cards/88801234/renew?project_id=1" \
  -H "Authorization: Bearer <session>" -H "Content-Type: application/json" -d '{"days": 30}'
```

跑测试（每个测试会话用独立的临时 SQLite 文件，无需准备数据库）：

```bash
python -m pytest tests/ -v
```

## 部署说明（SQLite）

低并发订阅管理场景 SQLite 完全够用，部署因此简化：

- **无需装数据库**：数据就是 `DATABASE_URL` 指向的一个文件（建议 `./data/cardlink.db`），目录不存在会自动创建；
- **WAL 模式**：服务启动时对每个连接开启 `PRAGMA journal_mode=WAL` + `foreign_keys=ON`，读写不互斥，单写多读无压力；运行时会多出 `*.db-wal` / `*.db-shm` 伴生文件，属正常现象；
- **备份 = 复制文件**：停服后复制 `cardlink.db`（或在线用 `sqlite3 cardlink.db ".backup backup.db"`）即完成备份，恢复同理；
- **升级**：拉代码后 `alembic upgrade head` 再重启即可。

## API 契约

三种身份（前两种是 API 密钥，供服务器间调用；面板会话给 /admin 用）：

| 身份 | 凭证 | 用途 |
|------|------|------|
| 服务运营方 | `X-Master-Key: <MASTER_KEY 环境变量>` | 项目自举/删除（服务器间调用） |
| 项目（查询面） | `Authorization: Bearer <项目 resolve_token>` | resolve 校验 |
| 面板管理员 | `Authorization: Bearer <登录颁发的 session token>` | /admin 面板；可访问 /api/projects 与 /api/cards*（全部管理操作） |

面板会话说明：`POST /api/auth/login` 用 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 换 session token（有效期 `ADMIN_SESSION_DAYS` 天）；用 session 调 `/api/cards*` 时必须加 `?project_id=` 指定目标项目，调 `/api/projects` 与 X-Master-Key 等效。`POST /api/auth/logout` 删除 session。

| 方法 & 路径 | 鉴权 | 说明 | 请求体 / 参数 | 响应要点 |
|---|---|---|---|---|
| `POST /api/auth/login` | 无 | 管理员登录 | `{username, password}` | 返回 `{token, expires_at}`；失败 401 |
| `POST /api/auth/logout` | Session | 退出登录 | — | 删除 session |
| `POST /api/projects` | Master 或 Session | 创建项目 | `{name, callback_url?}` | 返回 `resolve_token` |
| `GET /api/projects` | Master 或 Session | 项目列表 | — | 含完整 `resolve_token`（面板可反复查看复制） |
| `DELETE /api/projects/{id}` | Master 或 Session | 删除项目 | — | 级联删除其下所有授权；不存在 404 |
| `POST /api/cards` | Session+`?project_id=` | 创建授权 | `{card_key, days≥1, remark?}`，card_key 为纯数字 4~32 位交易账号 | 账号已存在返回 409 |
| `GET /api/cards` | Session+`?project_id=` | 列表 | `?status=active\|suspended\|revoked`、`?expiring_in=7d`（N 天内到期且未过期） | `{cards, count}` |
| `GET /api/cards/{key}` | Session+`?project_id=` | 详情 | — | 404 不存在或跨项目 |
| `POST /api/cards/{key}/renew` | Session+`?project_id=` | 续费 | `{days≥1}` | `expires_at = max(expires_at, now) + days` |
| `PATCH /api/cards/{key}` | Session+`?project_id=` | 状态变更 / 改备注 | `{action?: suspend\|resume\|revoke\|reset, remark?}`（至少给一项；只给 remark 则不变状态） | 非法流转返回 409 |
| `POST /api/resolve` | Bearer resolve_token | 校验账号授权 | `{card_key}` | 见下 |

状态机：`suspend` 仅 `active→suspended`；`resume` 仅 `suspended→active`；`revoke` 任意态→`revoked`；`reset` 任意态（含 revoked）→`active`，不动 `expires_at`（误吊销恢复用，期限靠续费调）。

`POST /api/resolve` 响应：

```json
{ "valid": true,  "reason": null, "card_key": "88801234", "status": "active", "expires_at": "..." }
```

`valid=false` 时 `reason` 取值：`not_found`（不存在或属其他项目）/ `suspended` / `revoked` / `expired`（此时 `status` 仍是 `active`，过期由 `expires_at` 表达）。

## 管理后台

服务自带一个零依赖的单页管理后台：起服务后浏览器访问 **`/admin`**（`/` 会自动重定向过去）。页面是 `static/admin.html` 单文件，原生 HTML/CSS/JS，不引任何 CDN，内网环境可直接用。

登录用**管理员账号密码**（`.env` 里的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`，单一管理员），登录成功颁发 session token（有效期 `ADMIN_SESSION_DAYS` 天，默认 7）存浏览器 localStorage，"退出登录"会调接口删除 session 并清除本地。登录后可管理**所有项目**的授权（顶部下拉切换项目）。

注意区分两套凭证：面板登录是给**人**用的（管理全部项目）；`X-Master-Key` / `resolve_token` 是给**服务器间调用**用的（见上面的 API 契约）。

功能清单：

- 授权管理（默认标签页）：顶部项目下拉选择器；添加客户授权（交易账号 + 授权天数 + 备注，账号纯数字校验）；列表按账号搜索、按状态 / `expiring_in` 筛选；到期列两行显示到期时间与"剩余 X 天"（已过期显示"已过期 X 天"），剩余 ≤7 天橙色、已过期红色；续费（输入天数）；暂停/恢复；吊销（二次确认）；重置（suspended/revoked 行显示，二次确认，恢复 active 不动到期时间）；点击备注直接修改；账号一键复制
- 项目管理（标签页）：项目列表（每个项目的 resolve_token 掩码展示 `前3位*****后4位`，旁边一键复制完整 token，可反复查看）；创建项目（name + callback_url），创建成功提示框同样掩码 + 复制；删除项目（红色按钮 → 第一次确认 → 第二次需手动输入"确认删除"四个字，级联删除项目下所有授权）

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
  "card_key": "88801234", "expires_at": "...", "project_id": 1 }
```

标记列只在推送成功（2xx）后写入；失败仅记日志，下轮扫描自动重试。项目无 `callback_url` 或授权已 `revoked` 不推送。

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `DATABASE_URL` | 是 | 无（缺失拒启） | SQLite 数据库文件路径，如 `./data/cardlink.db`（也接受 `sqlite:///...` 前缀写法） |
| `MASTER_KEY` | 是 | 无（缺失拒启） | 项目自举接口主密钥（服务器间调用） |
| `ADMIN_USERNAME` | 是 | 无（缺失拒启） | 面板管理员用户名（/admin 登录） |
| `ADMIN_PASSWORD` | 是 | 无（缺失拒启） | 面板管理员密码（/admin 登录） |
| `ADMIN_SESSION_DAYS` | 否 | `7` | 面板会话有效天数 |
| `SCAN_INTERVAL_MINUTES` | 否 | `60` | 到期扫描间隔（分钟） |
| `EXPIRING_DAYS` | 否 | `7` | 临期阈值（天） |
