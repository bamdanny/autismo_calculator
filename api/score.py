from fastapi import FastAPI, HTTPException

from calculator_core import build_result_from_snapshot, load_base_dataset, parse_json_snapshot

app = FastAPI(title="Autismo Conviction API")


@app.get("/")
def health() -> dict:
    return {
        "status": "ok",
        "message": "POST a snapshot JSON payload to /api/score for scoring.",
    }


@app.post("/")
def score_snapshot(payload: dict) -> dict:
    try:
        snapshot = parse_json_snapshot(payload)
        base_dataset = load_base_dataset()
        result = build_result_from_snapshot(snapshot, base_dataset)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "score": round(result["score"], 2),
        "orientation": result["orientation"],
        "coverage": round(result["overall_coverage"], 4),
        "category_pressures": result["category_pressures"],
        "rationale": result["rationale_lines"],
        "unknown_fields": result["unknown_fields"],
        "timestamp": result["timestamp"],
    }
