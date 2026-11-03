import os
from pathlib import Path
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
import yaml
from jobhunt.core.db import engine

app = FastAPI(title="Job Hunt API")


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail={"status": "error", "db": str(e)}
        )


@app.get("/companies")
async def get_companies() -> List[Dict[str, Any]]:
    possible_paths = [
        Path("companies.yaml"),
        Path("/app/companies.yaml"),
        Path(os.getenv("COMPANIES_FILE", "")),
        Path(__file__).resolve().parent.parent.parent.parent / "companies.yaml",
    ]
    target = next((p for p in possible_paths if p.is_file()), None)
    if not target:
        raise HTTPException(status_code=404, detail="companies.yaml not found")

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    comps = data.get("companies", [])
    result = [
        {
            "name": c.get("name"),
            "tier": c.get("tier"),
            "coverage_layer": c.get("coverage_layer"),
            "feed_url_verified": c.get("feed_url_verified"),
        }
        for c in comps
    ]
    return result
