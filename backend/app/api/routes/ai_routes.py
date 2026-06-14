from fastapi import APIRouter, HTTPException, Query

from app.db import session_scope


router = APIRouter()


@router.get("/ai-models")
def ai_models():
    """List configured AI models with their status."""
    from app.ai.service import list_ai_model_status, is_ai_enabled
    with session_scope() as session:
        models = list_ai_model_status(session)
    return {
        "enabled": is_ai_enabled(),
        "models": models,
    }


@router.post("/ai-predictions/run")
async def ai_predictions_run(
    match_id: str = Query(..., description="Match ID to predict"),
    model_version: str | None = Query(None, description="Optional model version. If omitted, runs all enabled models."),
    force: bool = Query(False, description="Force re-run even if a successful prediction already exists"),
):
    """Run AI prediction for a match. If model_version is specified, runs that model only; otherwise runs all enabled models."""
    from app.ai.service import run_ai_prediction, run_ai_predictions_for_match, is_ai_enabled
    if not is_ai_enabled():
        raise HTTPException(status_code=400, detail="AI prediction is not enabled. Set ENABLE_AI_PREDICTION=true.")
    with session_scope() as session:
        if model_version:
            result = await run_ai_prediction(session, match_id, model_version, force=force)
            if result.get("status") == "error":
                raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
            return result
        else:
            results = await run_ai_predictions_for_match(session, match_id, force=force)
            return {"results": results}


@router.post("/ai-predictions/run-match")
async def ai_predictions_run_match(
    match_id: str,
    force: bool = Query(False, description="Force re-run even if a successful prediction already exists"),
):
    """Run all enabled AI models for a match."""
    from app.ai.service import run_ai_predictions_for_match, is_ai_enabled
    if not is_ai_enabled():
        raise HTTPException(status_code=400, detail="AI prediction is not enabled. Set ENABLE_AI_PREDICTION=true.")
    with session_scope() as session:
        results = await run_ai_predictions_for_match(session, match_id, force=force)
    return {"results": results}


@router.post("/ai-predictions/run-all")
async def ai_predictions_run_all(stage: str | None = None, limit: int = 10, only_missing: bool = True, retry_failed: bool = False):
    """Batch run AI predictions for multiple matches."""
    from app.ai.service import run_ai_predictions_batch, is_ai_enabled
    from app.config import settings
    if not is_ai_enabled():
        raise HTTPException(status_code=400, detail="AI prediction is not enabled. Set ENABLE_AI_PREDICTION=true.")
    if limit < 1 or limit > settings.ai_run_all_max_limit:
        raise HTTPException(status_code=400, detail=f"limit must be between 1 and {settings.ai_run_all_max_limit}")
    with session_scope() as session:
        results = await run_ai_predictions_batch(session, stage=stage, limit=limit, only_missing=only_missing, retry_failed=retry_failed)
    return {"results": results, "count": len(results)}


@router.get("/ai-predictions")
def ai_predictions_get(match_id: str):
    """Get all AI predictions for a match."""
    from app.ai.service import get_ai_predictions
    with session_scope() as session:
        predictions = get_ai_predictions(session, match_id)
    return {"match_id": match_id, "predictions": predictions}


@router.post("/ensemble/run")
async def ensemble_run(match_id: str):
    """Generate ensemble prediction for a match."""
    from app.ai.ensemble import compute_ensemble
    with session_scope() as session:
        result = compute_ensemble(session, match_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "Unknown error"))
    return result


@router.get("/ensemble")
def ensemble_get(match_id: str):
    """Get ensemble prediction history for a match."""
    from app.ai.ensemble import get_ensemble_predictions
    with session_scope() as session:
        predictions = get_ensemble_predictions(session, match_id)
    return {"match_id": match_id, "predictions": predictions}


@router.get("/ai-evaluation")
def ai_evaluation():
    """Evaluate AI and ensemble predictions against actual results."""
    from app.ai.evaluation import evaluate_ai_predictions
    with session_scope() as session:
        return evaluate_ai_predictions(session)
