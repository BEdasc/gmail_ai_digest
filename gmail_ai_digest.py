"""
Agent IA de veille technologique — Résumé quotidien Gmail
=========================================================

Cet agent se connecte à un compte Gmail via l'API Google,
détecte les emails relatifs à l'actualité de l'IA, filtre
les sujets non pertinents (levées de fonds, transferts,
rachats) et produit un résumé quotidien structuré.

Framework : PydanticAI (structured output + dependency injection)
Backend   : Anthropic Claude Sonnet 4.6

Prérequis :
    pip install pydantic-ai[anthropic] google-auth-oauthlib google-api-python-client

Configuration :
    1. Créer un projet Google Cloud Console
    2. Activer l'API Gmail
    3. Créer des identifiants OAuth 2.0 (type "Application de bureau")
    4. Télécharger le fichier credentials.json
    5. Définir ANTHROPIC_API_KEY dans l'environnement

Premier lancement :
    Le script ouvrira un navigateur pour l'authentification OAuth.
    Le token sera ensuite sauvegardé dans token.json pour les
    exécutions suivantes (pas de ré-authentification nécessaire).
"""

from __future__ import annotations

import asyncio
import base64

from dotenv import load_dotenv
load_dotenv()
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

# ---------------------------------------------------------------------------
# Google API imports
# ---------------------------------------------------------------------------
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Portées OAuth Gmail (lecture seule)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Chemin vers les fichiers d'authentification
CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")

# Nombre maximum d'emails à récupérer par exécution
MAX_EMAILS = 50

# Mots-clés pour identifier les emails liés à l'IA
# Recherche dans le sujet ET le corps pour capturer les newsletters à sujet générique
AI_KEYWORDS_QUERY = (
    "(AI OR artificial intelligence OR LLM OR GPT OR Claude OR "
    "machine learning OR deep learning OR neural OR transformer OR "
    "IA OR intelligence artificielle OR modèle de langage OR "
    "ChatGPT OR Gemini OR Mistral OR Llama OR diffusion OR "
    "generative OR génératif OR veille IA)"
)

# Expéditeurs connus de newsletters IA — capturés inconditionnellement
KNOWN_SENDERS_QUERY = (
    "from:(aiforwork OR alphasignal OR neatprompts OR deeplearning.ai)"
)

# Requête combinée : mots-clés OU expéditeurs connus
GMAIL_QUERY = f"({AI_KEYWORDS_QUERY} OR {KNOWN_SENDERS_QUERY})"

# Sujets à exclure du résumé (détectés par l'agent)
EXCLUDED_TOPICS = [
    "levée de fonds", "fundraising", "funding round", "series A", "series B",
    "IPO", "acquisition", "rachat", "merger", "takeover",
    "nomination", "transfert", "hire", "appointed", "rejoint", "quitte",
]


# ---------------------------------------------------------------------------
# Modèles de données (Pydantic)
# ---------------------------------------------------------------------------

class ArticleSummary(BaseModel):
    """Résumé d'un article ou email individuel sur l'IA."""

    titre: str = Field(description="Titre concis de l'actualité (max 120 caractères)")
    source: str = Field(description="Nom de la newsletter ou de l'expéditeur")
    categorie: str = Field(
        description=(
            "Catégorie parmi : 'modèles' (nouveaux LLM, benchmarks), "
            "'outils' (frameworks, bibliothèques, API), "
            "'recherche' (papers, avancées scientifiques), "
            "'applications' (cas d'usage, déploiements), "
            "'regulation' (lois, normes, éthique), "
            "'autre'"
        )
    )
    resume: str = Field(
        description="Résumé en 2-3 phrases des points clés techniques"
    )
    pertinence: int = Field(
        ge=1, le=5,
        description="Score de pertinence 1-5 (5 = très pertinent pour un développeur/technicien IA)"
    )
    url: Optional[str] = Field(
        default=None,
        description="URL de l'article original si disponible dans l'email"
    )


class DailyDigest(BaseModel):
    """Résumé quotidien structuré de la veille IA."""

    date: str = Field(description="Date du résumé au format YYYY-MM-DD")
    nb_emails_analyses: int = Field(description="Nombre d'emails analysés")
    nb_articles_retenus: int = Field(description="Nombre d'articles retenus après filtrage")
    articles: list[ArticleSummary] = Field(
        description="Liste des articles retenus, triés par pertinence décroissante"
    )
    synthese_globale: str = Field(
        description=(
            "Synthèse en 3-5 phrases des tendances majeures du jour. "
            "Focus sur les avancées techniques, pas sur le business."
        )
    )
    top_3_a_retenir: list[str] = Field(
        description="Les 3 informations les plus importantes à retenir aujourd'hui"
    )


# ---------------------------------------------------------------------------
# Dépendances de l'agent
# ---------------------------------------------------------------------------

@dataclass
class GmailDigestDeps:
    """Dépendances injectées dans l'agent via PydanticAI."""

    gmail_service: object  # googleapiclient Resource
    target_date: datetime = field(default_factory=datetime.now)
    max_emails: int = MAX_EMAILS


# ---------------------------------------------------------------------------
# Authentification Gmail
# ---------------------------------------------------------------------------

def authenticate_gmail() -> object:
    """Authentification OAuth2 et construction du service Gmail.

    Returns:
        Service Gmail API prêt à l'emploi.
    """
    creds: Optional[Credentials] = None

    # Charger le token existant
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), GMAIL_SCOPES)

    # Rafraîchir ou lancer le flux OAuth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Fichier '{CREDENTIALS_FILE}' introuvable. "
                    "Téléchargez-le depuis Google Cloud Console "
                    "(API & Services > Identifiants > OAuth 2.0)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Sauvegarder le token pour les prochaines exécutions
        TOKEN_FILE.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ---------------------------------------------------------------------------
# Récupération des emails
# ---------------------------------------------------------------------------

def fetch_ai_emails(
    service: object,
    target_date: datetime,
    max_results: int = MAX_EMAILS,
) -> list[dict]:
    """Récupère les emails liés à l'IA reçus à la date cible.

    Args:
        service: Service Gmail API authentifié.
        target_date: Date des emails à récupérer.
        max_results: Nombre maximum d'emails.

    Returns:
        Liste de dictionnaires {subject, from, date, body_snippet}.
    """
    # Construire la requête Gmail (after/before en epoch)
    date_start = target_date.replace(hour=0, minute=0, second=0)
    date_end = date_start + timedelta(days=1)

    query = (
        f"{GMAIL_QUERY} "
        f"after:{int(date_start.timestamp())} "
        f"before:{int(date_end.timestamp())}"
    )

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_results)
        .execute()
    )

    messages = results.get("messages", [])
    if not messages:
        return []

    emails = []
    for msg_ref in messages:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_ref["id"], format="full")
            .execute()
        )
        emails.append(_parse_email(msg))

    return emails


def _parse_email(msg: dict) -> dict:
    """Extrait les métadonnées et le corps d'un email Gmail.

    Args:
        msg: Message brut de l'API Gmail.

    Returns:
        Dictionnaire structuré avec subject, from, date, body.
    """
    headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

    # Extraire le corps du message
    body = _extract_body(msg["payload"])

    return {
        "subject": headers.get("subject", "(sans sujet)"),
        "from": headers.get("from", "(inconnu)"),
        "date": headers.get("date", ""),
        "body": body[:5000],  # Limiter la taille pour le contexte LLM
        "snippet": msg.get("snippet", ""),
    }


def _extract_body(payload: dict) -> str:
    """Extrait le texte du corps d'un email (text/plain prioritaire).

    Args:
        payload: Payload du message Gmail.

    Returns:
        Texte du corps de l'email.
    """
    # Cas simple : corps directement dans le payload
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    # Cas multipart : chercher text/plain puis text/html
    parts = payload.get("parts", [])
    text_parts = []
    html_parts = []

    for part in parts:
        mime = part.get("mimeType", "")
        if mime == "text/plain" and part.get("body", {}).get("data"):
            text_parts.append(
                base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            )
        elif mime == "text/html" and part.get("body", {}).get("data"):
            html_parts.append(
                base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
            )
        elif "parts" in part:
            # Récursion pour les multipart imbriqués
            nested = _extract_body(part)
            if nested:
                text_parts.append(nested)

    if text_parts:
        return "\n".join(text_parts)

    # Fallback : nettoyer le HTML basiquement
    if html_parts:
        html = "\n".join(html_parts)
        return re.sub(r"<[^>]+>", " ", html).strip()

    return ""


# ---------------------------------------------------------------------------
# Agent PydanticAI — Classification et résumé
# ---------------------------------------------------------------------------

digest_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=DailyDigest,
    deps_type=GmailDigestDeps,
    instructions="""\
Tu es un analyste expert en veille technologique IA. Tu analyses des emails
de newsletters et d'alertes pour produire un résumé quotidien structuré.

RÈGLES DE FILTRAGE :
- INCLURE : nouveaux modèles, benchmarks, frameworks, bibliothèques, API,
  papers de recherche, avancées techniques, cas d'usage innovants, tutoriels,
  réglementation IA (AI Act, etc.), outils de développement, agents IA.
- EXCLURE SYSTÉMATIQUEMENT : levées de fonds, rounds de financement, IPO,
  acquisitions, rachats, nominations, transferts de personnes, départs,
  embauches, promotions, vie d'entreprise non technique.
- En cas de doute, inclure avec un score de pertinence bas (1-2).

CONSIGNES DE RÉSUMÉ :
- Résumer chaque article en 2-3 phrases techniques et factuelles.
- Utiliser un vocabulaire technique précis (pas de vulgarisation excessive).
- Indiquer les URLs si présentes dans le corps de l'email.
- Trier les articles par pertinence décroissante.
- La synthèse globale doit dégager les tendances techniques du jour.
- Les top 3 doivent être des informations actionnables pour un développeur IA.
- Répondre en français.
""",
    retries=2,
)


@digest_agent.tool
async def recuperer_emails(ctx: RunContext[GmailDigestDeps]) -> str:
    """Récupère les emails liés à l'IA depuis Gmail pour la date cible.

    Returns:
        Les emails formatés en texte pour analyse.
    """
    emails = fetch_ai_emails(
        service=ctx.deps.gmail_service,
        target_date=ctx.deps.target_date,
        max_results=ctx.deps.max_emails,
    )

    if not emails:
        return "Aucun email lié à l'IA trouvé pour cette date."

    # Formater les emails pour le contexte LLM
    formatted = []
    for i, email in enumerate(emails, 1):
        formatted.append(
            f"=== EMAIL {i}/{len(emails)} ===\n"
            f"De : {email['from']}\n"
            f"Sujet : {email['subject']}\n"
            f"Date : {email['date']}\n"
            f"---\n"
            f"{email['body']}\n"
        )

    return "\n\n".join(formatted)


# ---------------------------------------------------------------------------
# Exécution principale
# ---------------------------------------------------------------------------

async def generate_digest(
    target_date: Optional[datetime] = None,
    max_emails: int = MAX_EMAILS,
) -> DailyDigest:
    """Génère le résumé quotidien de veille IA.

    Args:
        target_date: Date cible (défaut : hier).
        max_emails: Nombre max d'emails à analyser.

    Returns:
        DailyDigest validé par Pydantic.
    """
    if target_date is None:
        target_date = datetime.now() - timedelta(days=1)

    # Authentification Gmail
    print(f"🔐 Connexion à Gmail...")
    gmail_service = authenticate_gmail()

    # Injection des dépendances
    deps = GmailDigestDeps(
        gmail_service=gmail_service,
        target_date=target_date,
        max_emails=max_emails,
    )

    date_str = target_date.strftime("%Y-%m-%d")
    print(f"📧 Analyse des emails du {date_str}...")

    # Exécution de l'agent
    result = await digest_agent.run(
        f"Récupère les emails IA du {date_str} et produis le résumé quotidien.",
        deps=deps,
    )

    return result.output


def print_digest(digest: DailyDigest) -> None:
    """Affiche le résumé dans un format lisible en console.

    Args:
        digest: Le résumé quotidien à afficher.
    """
    print("\n" + "=" * 70)
    print(f"  📰 VEILLE IA — {digest.date}")
    print(f"  📧 {digest.nb_emails_analyses} emails analysés → "
          f"{digest.nb_articles_retenus} articles retenus")
    print("=" * 70)

    print("\n🔝 TOP 3 À RETENIR :")
    for i, item in enumerate(digest.top_3_a_retenir, 1):
        print(f"  {i}. {item}")

    print(f"\n📊 SYNTHÈSE : {digest.synthese_globale}")

    print("\n" + "-" * 70)
    print("  ARTICLES DÉTAILLÉS")
    print("-" * 70)

    for article in digest.articles:
        stars = "★" * article.pertinence + "☆" * (5 - article.pertinence)
        print(f"\n  [{article.categorie.upper()}] {article.titre}")
        print(f"  Source : {article.source} | Pertinence : {stars}")
        print(f"  {article.resume}")
        if article.url:
            print(f"  🔗 {article.url}")

    print("\n" + "=" * 70)


def save_digest_json(digest: DailyDigest, output_dir: Path = Path("digests")) -> Path:
    """Sauvegarde le résumé en JSON pour archivage.

    Args:
        digest: Le résumé quotidien.
        output_dir: Répertoire de sortie.

    Returns:
        Chemin du fichier JSON créé.
    """
    output_dir.mkdir(exist_ok=True)
    filepath = output_dir / f"digest_{digest.date}.json"
    filepath.write_text(
        digest.model_dump_json(indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return filepath


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Agent de veille IA — Résumé quotidien Gmail"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Date cible au format YYYY-MM-DD (défaut : hier)",
    )
    parser.add_argument(
        "--max-emails",
        type=int,
        default=MAX_EMAILS,
        help=f"Nombre max d'emails à analyser (défaut : {MAX_EMAILS})",
    )
    parser.add_argument(
        "--save-json",
        action="store_true",
        help="Sauvegarder le résumé en JSON dans ./digests/",
    )

    args = parser.parse_args()

    # Parser la date
    target = None
    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d")

    # Lancer l'agent
    try:
        digest = asyncio.run(generate_digest(target_date=target, max_emails=args.max_emails))
        print_digest(digest)

        if args.save_json:
            path = save_digest_json(digest)
            print(f"\n💾 Résumé sauvegardé : {path}")

    except FileNotFoundError as e:
        print(f"\n❌ Erreur de configuration : {e}")
        print("   Consultez la documentation en tête de ce fichier.")
    except Exception as e:
        print(f"\n❌ Erreur : {e}")
        raise
