from fastapi import APIRouter

from app.api.routes.consultation import router as consultation_router
from app.api.routes.gbp import public_router as gbp_public_router
from app.api.routes.gbp import router as gbp_router
from app.api.routes.prescription import router as prescription_router

api_router = APIRouter()
api_router.include_router(consultation_router)
api_router.include_router(prescription_router)
api_router.include_router(gbp_router)
api_router.include_router(gbp_public_router)
