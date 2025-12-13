# QA Test Plan Agent – Hackathon

Agent IA qui lit une User Story Jira (+ Xray + Bitbucket) et génère un plan de tests.

## Préparation

```bash
python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env     # et remplir les valeurs si besoin
```

## Lancer le backend
```bash
# Option A – run with the module path (recommended for reload reliability)
python -m uvicorn hackathon.backend.main:app --reload

# Option B – install editable, then run (recommended for editable import path)
pip install -e .
python -m uvicorn hackathon.backend.main:app --reload
```

Le backend écoute sur http://localhost:8000.

Test rapide :
```bash
curl http://localhost:8000/health
```

Si vous rencontrez des erreurs d'import liées à `hackathon` pendant le reload
de Uvicorn, utilisez l'une des méthodes suivantes :

- Installez le package en editable mode :
```bash
pip install -e .
```
- Ou lancez Uvicorn via `python -m` pour garantir le bon chemin d'import :
```bash
python -m uvicorn hackathon.backend.main:app --reload
```

## Lancer le frontend

# Depuis frontend/ :
```bash
cd frontend
python -m http.server 8080
```
Puis ouvrir http://localhost:8080 dans le navigateur.
