# Autismo Conviction Calculator

Local Streamlit app that ingests JSON/CSV market snapshots, contextualizes them with `data/base_dataset.json`, and emits:

- conviction score (0–100)
- deterministic orientation (`Short idea`, `No trade`, `Long idea`)
- rationale + category contributions
- persistent ledger row in `data/ledger.csv`

## Run locally

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Start Streamlit:

```bash
streamlit run streamlit_app.py
```

3. Open the URL printed by Streamlit (normally `http://localhost:8501`).

## Deploying: Vercel vs Streamlit hosting

Short answer: **Vercel is not a good fit for this app in its current form**.

Why:

- This is a long-running Streamlit process, while Vercel is optimized for serverless functions/static sites.
- The app writes snapshots/ledger files to local disk (`data/snapshots`, `data/ledger.csv`), but Vercel’s filesystem is ephemeral.
- Streamlit apps are typically deployed on Streamlit Community Cloud, Render, Railway, or a VM/container host.

If you still want Vercel, you’d need a larger redesign (separate frontend + API + external database/object storage).

## Current data files

- `data/base_dataset.json`: versioned normalization/directionality metadata.
- `data/snapshots/`: uploaded raw snapshots (created automatically).
- `data/ledger.csv`: persisted scoring history (created automatically).
