"""
cardlink-service：通用卡号授权服务。

启动：
  DATABASE_URL=... MASTER_KEY=... uvicorn main:app --port 8000
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse

import config  # noqa: F401  导入即校验必填环境变量，缺失拒启
from database import close_db, init_db
from routers import cards, projects, resolve
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()
    close_db()


app = FastAPI(title="cardlink-service", version="1.0.0", lifespan=lifespan)
app.include_router(projects.router)
app.include_router(cards.router)
app.include_router(resolve.router)


ADMIN_HTML = Path(__file__).resolve().parent / "static" / "admin.html"


@app.get("/", include_in_schema=False)
def index():
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
def admin_page():
    """内置单页管理后台（纯静态 H5，无需构建）"""
    return FileResponse(ADMIN_HTML)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
