"""
Projetos Routes
===============

CRUD endpoints for Projetos (mineral / supply-chain projects).
"""

from datetime import datetime
from typing import Annotated

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import AuthenticatedUser, Database, Pagination
from app.core.logging import get_logger
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.projetos import (
    ProjetoCreate,
    ProjetoListResponse,
    ProjetoResponse,
    ProjetoStatus,
    ProjetoType,
    ProjetoUpdate,
)

logger = get_logger(__name__)

router = APIRouter()

COLLECTION_NAME = "projetos"


# =============================================================================
# Helper Functions
# =============================================================================

def validate_object_id(id: str) -> ObjectId:
    """Validate and convert string to ObjectId."""
    try:
        return ObjectId(id)
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {id}"
        )


async def get_projeto_or_404(
    db: AsyncIOMotorDatabase,
    projeto_id: str
) -> dict:
    """Get projeto by ID or raise 404."""
    oid = validate_object_id(projeto_id)
    projeto = await db[COLLECTION_NAME].find_one({"_id": oid})
    if not projeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Projeto not found: {projeto_id}"
        )
    return projeto


async def count_analises(db: AsyncIOMotorDatabase, projeto_id: str) -> int:
    """Count análises linked to a projeto."""
    return await db["analises"].count_documents({"projeto_id": projeto_id})


# =============================================================================
# CRUD Endpoints
# =============================================================================

@router.post(
    "",
    response_model=ProjetoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new projeto",
    description="Create a new mineral / supply-chain project."
)
async def create_projeto(
    projeto: ProjetoCreate,
    db: Database,
    user: AuthenticatedUser,
):
    """Create a new projeto."""
    now = datetime.utcnow()

    doc = projeto.model_dump()
    doc["created_by"] = user.sub
    doc["created_at"] = now
    doc["updated_at"] = now

    result = await db[COLLECTION_NAME].insert_one(doc)
    created = await db[COLLECTION_NAME].find_one({"_id": result.inserted_id})
    created["total_analises"] = 0

    logger.info(
        "Projeto created",
        projeto_id=str(result.inserted_id),
        nome=projeto.nome,
        user_id=user.sub,
    )

    return ProjetoResponse(**created)


@router.get(
    "",
    response_model=PaginatedResponse[ProjetoListResponse],
    summary="List projetos",
    description="List projetos with pagination and filtering."
)
async def list_projetos(
    db: Database,
    user: AuthenticatedUser,
    pagination: Pagination,
    status_filter: ProjetoStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by status"
    ),
    tipo: ProjetoType | None = Query(
        default=None,
        description="Filter by type"
    ),
    uf: str | None = Query(
        default=None,
        min_length=2,
        max_length=2,
        description="Filter by UF"
    ),
    search: str | None = Query(
        default=None,
        min_length=2,
        description="Search in nome and descricao"
    ),
):
    """List projetos with filters and pagination."""
    query: dict = {}

    if status_filter:
        query["status"] = status_filter.value
    if tipo:
        query["tipo"] = tipo.value
    if uf:
        query["uf"] = uf.upper()
    if search:
        query["$or"] = [
            {"nome": {"$regex": search, "$options": "i"}},
            {"descricao": {"$regex": search, "$options": "i"}},
        ]

    total = await db[COLLECTION_NAME].count_documents(query)
    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 1

    cursor = db[COLLECTION_NAME].find(query)
    cursor = cursor.sort("updated_at", -1)
    cursor = cursor.skip(pagination.skip).limit(pagination.page_size)

    items = []
    async for doc in cursor:
        doc["total_analises"] = await count_analises(db, str(doc["_id"]))
        items.append(ProjetoListResponse(**doc))

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get(
    "/{projeto_id}",
    response_model=ProjetoResponse,
    summary="Get projeto by ID",
    description="Get detailed information about a specific projeto."
)
async def get_projeto(
    projeto_id: str,
    db: Database,
    user: AuthenticatedUser,
):
    """Get projeto by ID."""
    projeto = await get_projeto_or_404(db, projeto_id)
    projeto["total_analises"] = await count_analises(db, projeto_id)
    return ProjetoResponse(**projeto)


@router.put(
    "/{projeto_id}",
    response_model=ProjetoResponse,
    summary="Update projeto",
    description="Update an existing projeto."
)
async def update_projeto(
    projeto_id: str,
    update: ProjetoUpdate,
    db: Database,
    user: AuthenticatedUser,
):
    """Update an existing projeto."""
    await get_projeto_or_404(db, projeto_id)

    update_data = update.model_dump(exclude_unset=True, exclude_none=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    update_data["updated_at"] = datetime.utcnow()

    oid = validate_object_id(projeto_id)
    await db[COLLECTION_NAME].update_one({"_id": oid}, {"$set": update_data})

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated["total_analises"] = await count_analises(db, projeto_id)

    logger.info(
        "Projeto updated",
        projeto_id=projeto_id,
        fields=list(update_data.keys()),
        user_id=user.sub,
    )

    return ProjetoResponse(**updated)


@router.delete(
    "/{projeto_id}",
    response_model=SuccessResponse,
    summary="Delete projeto",
    description="Delete a projeto and optionally its linked análises."
)
async def delete_projeto(
    projeto_id: str,
    db: Database,
    user: AuthenticatedUser,
    cascade: bool = Query(
        default=False,
        description="Also delete linked análises"
    ),
):
    """Delete a projeto."""
    projeto = await get_projeto_or_404(db, projeto_id)
    analises_count = await count_analises(db, projeto_id)

    if analises_count > 0 and not cascade:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Projeto has {analises_count} linked análises. Use cascade=true to delete all."
        )

    oid = validate_object_id(projeto_id)

    if cascade and analises_count > 0:
        await db["analises"].delete_many({"projeto_id": projeto_id})
        logger.info(
            "Análises deleted (cascade)",
            projeto_id=projeto_id,
            count=analises_count,
        )

    await db[COLLECTION_NAME].delete_one({"_id": oid})

    logger.info(
        "Projeto deleted",
        projeto_id=projeto_id,
        nome=projeto.get("nome"),
        user_id=user.sub,
        cascade=cascade,
    )

    return SuccessResponse(
        message="Projeto deleted successfully" + (
            f" (including {analises_count} análises)" if cascade and analises_count > 0 else ""
        )
    )


# =============================================================================
# Additional Endpoints
# =============================================================================

@router.get(
    "/{projeto_id}/analises",
    response_model=list[dict],
    summary="List análises of a projeto",
    description="Get all análises linked to a specific projeto."
)
async def list_projeto_analises(
    projeto_id: str,
    db: Database,
    user: AuthenticatedUser,
):
    """List análises of a projeto."""
    await get_projeto_or_404(db, projeto_id)

    cursor = db["analises"].find({"projeto_id": projeto_id})
    cursor = cursor.sort("updated_at", -1)

    analises = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        doc["total_jazidas"] = len(doc.get("jazidas_selecionadas", []))
        doc["total_favoritos"] = sum(
            1 for j in doc.get("jazidas_selecionadas", [])
            if j.get("favorito")
        )
        analises.append(doc)

    return analises


@router.post(
    "/{projeto_id}/duplicate",
    response_model=ProjetoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate projeto",
    description="Create a copy of an existing projeto."
)
async def duplicate_projeto(
    projeto_id: str,
    db: Database,
    user: AuthenticatedUser,
    include_analises: bool = Query(
        default=False,
        description="Also duplicate linked análises"
    ),
):
    """Duplicate a projeto."""
    original = await get_projeto_or_404(db, projeto_id)

    now = datetime.utcnow()

    copy = dict(original)
    del copy["_id"]
    copy["nome"] = f"{copy['nome']} (cópia)"
    copy["status"] = ProjetoStatus.PLANEJAMENTO.value
    copy["created_by"] = user.sub
    copy["created_at"] = now
    copy["updated_at"] = now

    result = await db[COLLECTION_NAME].insert_one(copy)
    new_projeto_id = str(result.inserted_id)

    analises_copied = 0
    if include_analises:
        cursor = db["analises"].find({"projeto_id": projeto_id})
        async for analise in cursor:
            del analise["_id"]
            analise["projeto_id"] = new_projeto_id
            analise["titulo"] = f"{analise['titulo']} (cópia)"
            analise["created_by"] = user.sub
            analise["created_at"] = now
            analise["updated_at"] = now
            await db["analises"].insert_one(analise)
            analises_copied += 1

    created = await db[COLLECTION_NAME].find_one({"_id": result.inserted_id})
    created["total_analises"] = analises_copied

    logger.info(
        "Projeto duplicated",
        original_id=projeto_id,
        new_id=new_projeto_id,
        include_analises=include_analises,
        analises_copied=analises_copied,
        user_id=user.sub,
    )

    return ProjetoResponse(**created)
