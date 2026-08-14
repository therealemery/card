"""
服务配置：仅从环境变量读取，敏感项无默认值，缺失即拒启。

必填：
  DATABASE_URL  PostgreSQL 连接串，如 postgresql://user:pass@127.0.0.1:5432/cardlink
  MASTER_KEY    项目自举接口（/api/projects）的主密钥

可选：
  SCAN_INTERVAL_MINUTES  到期扫描间隔（分钟），默认 60
  EXPIRING_DAYS          剩余多少天内算"临期"，默认 7
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少必填环境变量 {name}，服务拒绝启动")
    return value


DATABASE_URL = _require("DATABASE_URL")
MASTER_KEY = _require("MASTER_KEY")

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
EXPIRING_DAYS = int(os.getenv("EXPIRING_DAYS", "7"))
