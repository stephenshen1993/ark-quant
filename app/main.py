from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from datasource.db import init_db
from app.routers import accounts, plans, positions, rankings

app = FastAPI(title="ark-quant dashboard")

init_db()

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(accounts.router)
app.include_router(accounts.current_router)
app.include_router(positions.router)
app.include_router(rankings.router)
app.include_router(plans.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "ark-quant dashboard — place index.html in app/static/"}
