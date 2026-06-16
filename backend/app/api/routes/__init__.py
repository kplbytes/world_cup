from fastapi import APIRouter

from app.api.routes.ai_routes import router as ai_router
from app.api.routes.dashboard_routes import router as dashboard_router
from app.api.routes.data_routes import router as data_router
from app.api.routes.scoring_routes import router as scoring_router
from app.api.routes.tournament_routes import router as tournament_router
from app.api.routes.workflow_routes import router as workflow_router
from app.api.routes.team_profile_routes import router as team_profile_router


router = APIRouter(prefix="/api")

router.include_router(dashboard_router)
router.include_router(scoring_router)
router.include_router(ai_router)
router.include_router(tournament_router)
router.include_router(data_router)
router.include_router(workflow_router)
router.include_router(team_profile_router)
