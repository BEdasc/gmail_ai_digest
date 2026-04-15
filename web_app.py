"""
Interface web pour l'agent de veille IA Gmail
==============================================

Développement local :
    uvicorn web_app:app --reload

VPS (via systemd, voir gmail-digest.service) :
    uvicorn web_app:app --host 127.0.0.1 --port 8000 --workers 1
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from gmail_ai_digest import generate_digest, save_digest_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
)
logger = logging.getLogger("gmail_digest.web")

# Chemins absolus — stables quel que soit le working directory (important sous systemd)
_BASE = Path(__file__).parent
STATIC_DIR = _BASE / "static"
DIGESTS_DIR = _BASE / "digests"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = FastAPI(title="Gmail AI Digest")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Historique — lecture des digests sauvegardés
# ---------------------------------------------------------------------------

@app.get("/api/digests/history")
async def get_history():
    """Liste les digests sauvegardés sur disque, triés par date décroissante."""
    if not DIGESTS_DIR.exists():
        return []

    entries = []
    for path in sorted(DIGESTS_DIR.glob("digest_*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries.append({
                "date": data["date"],
                "nb_articles": data.get("nb_articles_retenus", 0),
            })
        except Exception:
            continue

    return entries


@app.get("/api/digests/{date}")
async def get_digest_by_date(date: str):
    """Charge un digest existant depuis le disque (sans régénérer)."""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")

    path = DIGESTS_DIR / f"digest_{date}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Aucun digest sauvegardé pour le {date}.")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [data]  # array pour compatibilité avec renderDigests() côté client
    except Exception:
        raise HTTPException(status_code=500, detail="Erreur de lecture du digest.")


# ---------------------------------------------------------------------------
# Schémas de requête
# ---------------------------------------------------------------------------

class DigestRequest(BaseModel):
    date: Optional[str] = None          # YYYY-MM-DD  → digest d'un seul jour
    start_date: Optional[str] = None    # YYYY-MM-DD  ┐ digest d'une période
    end_date: Optional[str] = None      # YYYY-MM-DD  ┘
    max_emails: int = Field(default=50, ge=1, le=200)


# ---------------------------------------------------------------------------
# Endpoint principal — génération avec cache automatique
# ---------------------------------------------------------------------------

@app.post("/api/digest")
async def get_digest(req: DigestRequest):
    """Génère un ou plusieurs digests selon la date / période fournie.

    Si un digest JSON existe déjà pour la date demandée, il est retourné
    directement depuis le cache sans appeler Gmail ni Anthropic.
    """

    # Résoudre la liste de dates à traiter
    if req.date:
        try:
            dates = [datetime.strptime(req.date, "%Y-%m-%d")]
        except ValueError:
            raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")

    elif req.start_date and req.end_date:
        try:
            start = datetime.strptime(req.start_date, "%Y-%m-%d")
            end = datetime.strptime(req.end_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Format de date invalide. Utilisez YYYY-MM-DD.")
        if start > end:
            raise HTTPException(status_code=400, detail="start_date doit être avant end_date.")
        if (end - start).days > 6:
            raise HTTPException(status_code=400, detail="Période limitée à 7 jours maximum.")
        nb_days = (end - start).days + 1
        dates = [start + timedelta(days=i) for i in range(nb_days)]

    else:
        # Défaut : hier
        dates = [datetime.now() - timedelta(days=1)]

    # Générer (ou charger depuis cache) un digest par date
    results = []
    DIGESTS_DIR.mkdir(exist_ok=True)

    for target in dates:
        date_str = target.strftime("%Y-%m-%d")
        cached_path = DIGESTS_DIR / f"digest_{date_str}.json"

        # Servir depuis le cache si disponible
        if cached_path.exists():
            try:
                results.append(json.loads(cached_path.read_text(encoding="utf-8")))
                logger.info("Digest %s servi depuis le cache.", date_str)
                continue
            except Exception:
                pass  # fichier corrompu → régénérer

        # Générer et sauvegarder
        try:
            digest = await generate_digest(target_date=target, max_emails=req.max_emails)
            save_digest_json(digest, DIGESTS_DIR)
            results.append(digest.model_dump())
        except Exception:
            logger.exception("Erreur génération digest pour %s", date_str)
            raise HTTPException(status_code=500, detail="Erreur lors de la génération du digest.")

    return results
