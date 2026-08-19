"""
服务配置：仅从环境变量读取，敏感项无默认值，缺失即拒启。

必填：
  DATABASE_URL  SQLite 数据库文件路径，如 ./data/cardlink.db
                （也接受 sqlite:///./data/cardlink.db 写法，前缀会被剥掉）
  MASTER_KEY    项目自举接口（/api/projects）的主密钥

可选：
  SCAN_INTERVAL_MINUTES  到期扫描间隔（分钟），默认 60
  EXPIRING_DAYS          剩余多少天内算"临期"，默认 7
  ADMIN_SESSION_DAYS     面板管理员会话有效天数，默认 7

面板管理员（单一管理员，/admin 登录用）：
  ADMIN_USERNAME / ADMIN_PASSWORD  均必填无默认
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
ADMIN_USERNAME = _require("ADMIN_USERNAME")
ADMIN_PASSWORD = _require("ADMIN_PASSWORD")

SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
EXPIRING_DAYS = int(os.getenv("EXPIRING_DAYS", "7"))
ADMIN_SESSION_DAYS = int(os.getenv("ADMIN_SESSION_DAYS", "7"))


def sqlite_path() -> str:
    """从 DATABASE_URL 解析出 SQLite 文件路径（剥掉可选的 sqlite:/// 前缀）"""
    url = DATABASE_URL
    if url.startswith("sqlite:///"):
        url = url[len("sqlite:///"):]
    if "://" in url:
        raise RuntimeError(f"DATABASE_URL 只支持 SQLite 文件路径，收到: {DATABASE_URL}")
    return url
