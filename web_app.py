"""
Interface web pour l'agent de veille IA Gmail
==============================================

Développement local :
    uvicorn web_app:app --reload

VPS (via systemd, voir gmail-digest.service) :
    uvicorn web_app:app --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gmail_ai_digest import generate_digest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
)
logger = logging.getLogger("gmail_digest.web")

# Chemins absolus — stables quel que soit le working directory (important sous systemd)
_BASE = Path(__file__).parent
STATIC_DIR = _BASE / "static"

app = FastAPI(title="Gmail AI Digest")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Schémas de requête
# ---------------------------------------------------------------------------

class DigestRequest(BaseModel):
    date: Optional[str] = None          # YYYY-MM-DD  → digest d'un seul jour
    start_date: Optional[str] = None    # YYYY-MM-DD  ┐ digest d'une période
    end_date: Optional[str] = None      # YYYY-MM-DD  ┘
    max_emails: int = Field(default=50, ge=1, le=200)


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@app.post("/api/digest")
async def get_digest(req: DigestRequest):
    """Génère un ou plusieurs digests selon la date / période fournie."""

    # Résoudre la liste de dates à traiter
    if req.date:
        dates = [datetime.strptime(req.date, "%Y-%m-%d")]

    elif req.start_date and req.end_date:
        start = datetime.strptime(req.start_date, "%Y-%m-%d")
        end = datetime.strptime(req.end_date, "%Y-%m-%d")
        if start > end:
            raise HTTPException(status_code=400, detail="start_date doit être avant end_date.")
        if (end - start).days > 6:
            raise HTTPException(status_code=400, detail="Période limitée à 7 jours maximum.")
        nb_days = (end - start).days + 1
        dates = [start + timedelta(days=i) for i in range(nb_days)]

    else:
        # Défaut : hier
        dates = [datetime.now() - timedelta(days=1)]

    # Générer un digest par date
    results = []
    for target in dates:
        try:
            digest = await generate_digest(target_date=target, max_emails=req.max_emails)
            results.append(digest.model_dump())
        except Exception:
            logger.exception("Erreur génération digest pour %s", target.date())
            raise HTTPException(status_code=500, detail="Erreur lors de la génération du digest.")

    return results
