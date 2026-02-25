# 📰 Agent de Veille IA — Résumé Quotidien Gmail

Agent PydanticAI qui analyse les emails IA de votre Gmail et produit un résumé quotidien structuré.

## Prérequis

- Python 3.10+
- Un compte Google avec Gmail
- Une clé API Anthropic

## Installation

```bash
pip install -r requirements.txt
```

## Configuration Google Cloud

1. Aller sur [Google Cloud Console](https://console.cloud.google.com/)
2. Créer un nouveau projet (ou sélectionner un existant)
3. **API & Services** → **Bibliothèque** → Activer **Gmail API**
4. **API & Services** → **Identifiants** → **Créer des identifiants** → **ID client OAuth 2.0**
   - Type d'application : **Application de bureau**
   - Nom : `AI Digest Agent`
5. Télécharger le JSON → le renommer `credentials.json`
6. Placer `credentials.json` dans le même répertoire que le script

> **Note :** Au premier lancement, un navigateur s'ouvrira pour autoriser l'accès.
> Le token sera sauvegardé dans `token.json` pour les exécutions suivantes.

## Variable d'environnement

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Utilisation

```bash
# Résumé d'hier (défaut)
python gmail_ai_digest.py

# Résumé d'une date spécifique
python gmail_ai_digest.py --date 2026-02-18

# Limiter à 20 emails + sauvegarde JSON
python gmail_ai_digest.py --max-emails 20 --save-json

# Aide
python gmail_ai_digest.py --help
```

## Sortie

Le résumé inclut :
- **Top 3** des informations à retenir
- **Synthèse globale** des tendances techniques
- **Articles détaillés** classés par pertinence (★★★★★)
- **Catégories** : modèles, outils, recherche, applications, régulation

## Automatisation (cron)

```bash
# Chaque matin à 7h
0 7 * * * cd /chemin/vers/agent && ANTHROPIC_API_KEY="sk-ant-..." python gmail_ai_digest.py --save-json
```

## Architecture

```
gmail_ai_digest.py      # Agent principal
credentials.json        # OAuth Google (à créer)
token.json              # Token auto-généré au 1er lancement
digests/                # Résumés JSON archivés (--save-json)
  └── digest_2026-02-18.json
```
