# Autismo Conviction Calculator

Local Streamlit app that ingests JSON/CSV market snapshots, contextualizes them with `data/base_dataset.json`, and emits:

- conviction score (0–100)
- deterministic orientation (`Short idea`, `No trade`, `Long idea`)
- rationale + category contributions
- persistent ledger row in `data/ledger.csv`

## Run locally (full UI)

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start Streamlit:

```bash
streamlit run streamlit_app.py
```

3. Open the URL printed by Streamlit (normally `http://localhost:8501`).

## Vercel deployment (API environment)

Since you connected the GitHub repo to Vercel, this repo now includes a deployable serverless endpoint:

- `POST /api/score`

What Vercel deploys in this setup:

- ✅ JSON scoring API (stateless)
- ❌ Streamlit UI and local ledger persistence (Vercel filesystem is ephemeral)

### Example request

```bash
curl -X POST "https://<your-vercel-domain>/api/score" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {"symbol": "BTC", "as_of": "2026-01-01T00:00:00Z"},
    "derivatives": {"funding": 0.01, "oi_now_bil": 18.2},
    "catalysts": {"tilt": 0.3}
  }'
```

### Example response

```json
{
  "score": 54.72,
  "orientation": "No trade",
  "coverage": 0.29,
  "category_pressures": {
    "Derivatives": 0.07,
    "Liquidity / Levels": 0.0,
    "Spot / Flow": 0.0,
    "Catalysts": 0.11
  },
  "rationale": ["..."],
  "unknown_fields": [],
  "timestamp": "2026-01-01T00:00:00+00:00"
}
```

## Current data files

- `data/base_dataset.json`: versioned normalization/directionality metadata.
- `data/snapshots/`: uploaded raw snapshots (created automatically by Streamlit app).
- `data/ledger.csv`: persisted scoring history (created automatically by Streamlit app).
