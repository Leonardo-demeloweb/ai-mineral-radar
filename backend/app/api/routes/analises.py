"""
Análises Routes
===============

CRUD endpoints for Análises (supply analyses linked to projetos).

Suporta análises de:
- Materiais de mineração (jazidas - índice ANM)
- Produtos comerciais (empresas - índice CNPJ)
- Serviços (empresas - índice CNPJ)
- Híbrido (ambos)
"""

from datetime import datetime
from typing import Annotated
from uuid import uuid4

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.api.deps import AuthenticatedUser, Database, Pagination
from app.core.logging import get_logger
from app.schemas.common import PaginatedResponse, SuccessResponse
from app.schemas.analises import (
    AddFornecedorRequest,
    AnaliseCreate,
    AnaliseListResponse,
    AnaliseResponse,
    AnaliseStatus,
    AnaliseUpdate,
    CategoriaAnalise,
    TipoFonte,
    UpdateFornecedorRequest,
    VisibilidadeAnalise,
)

logger = get_logger(__name__)

router = APIRouter()

COLLECTION_NAME = "analises"


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


async def get_analise_or_404(
    db: AsyncIOMotorDatabase,
    analise_id: str
) -> dict:
    """Get análise by ID or raise 404."""
    oid = validate_object_id(analise_id)
    analise = await db[COLLECTION_NAME].find_one({"_id": oid})
    if not analise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Análise not found: {analise_id}"
        )
    return analise


async def verify_projeto_exists(db: AsyncIOMotorDatabase, projeto_id: str) -> None:
    """Verify that a projeto exists."""
    oid = validate_object_id(projeto_id)
    projeto = await db["projetos"].find_one({"_id": oid})
    if not projeto:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Projeto not found: {projeto_id}"
        )


def enrich_analise(doc: dict) -> dict:
    """Add computed fields to análise document.

    Only favorited fornecedores are exposed to the API consumer.
    Non-favorited items (e.g. from legacy auto-save) are stripped out.
    """
    all_fornecedores = doc.get("fornecedores", [])
    favoritos = [f for f in all_fornecedores if f.get("favorito")]

    doc["fornecedores"] = favoritos
    doc["total_fornecedores"] = len(favoritos)
    doc["total_jazidas"] = sum(
        1 for f in favoritos if f.get("tipo_fonte") == TipoFonte.ANM.value
    )
    doc["total_empresas"] = sum(
        1 for f in favoritos if f.get("tipo_fonte") == TipoFonte.CNPJ.value
    )
    doc["total_favoritos"] = len(favoritos)
    doc["total_aprovados"] = sum(1 for f in favoritos if f.get("aprovado") is True)
    doc["total_arquivos"] = len(doc.get("arquivos_kml", []))

    return doc


# =============================================================================
# CRUD Endpoints
# =============================================================================

@router.post(
    "",
    response_model=AnaliseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new análise",
    description="Create a new supply analysis linked to a projeto."
)
async def create_analise(
    analise: AnaliseCreate,
    db: Database,
    user: AuthenticatedUser,
):
    """Create a new análise."""
    await verify_projeto_exists(db, analise.projeto_id)

    now = datetime.utcnow()

    doc = analise.model_dump()
    doc["fornecedores"] = []
    doc["arquivos_kml"] = []
    doc["compartilhado_com"] = []
    doc["created_by"] = user.sub
    doc["created_at"] = now
    doc["updated_at"] = now

    result = await db[COLLECTION_NAME].insert_one(doc)
    created = await db[COLLECTION_NAME].find_one({"_id": result.inserted_id})
    created = enrich_analise(created)

    logger.info(
        "Análise created",
        analise_id=str(result.inserted_id),
        titulo=analise.titulo,
        categoria=analise.categoria.value,
        termo_busca=analise.termo_busca,
        projeto_id=analise.projeto_id,
        user_id=user.sub,
    )

    return AnaliseResponse(**created)


@router.get(
    "",
    response_model=PaginatedResponse[AnaliseListResponse],
    summary="List análises",
    description="List análises with pagination and filtering."
)
async def list_analises(
    db: Database,
    user: AuthenticatedUser,
    pagination: Pagination,
    projeto_id: str | None = Query(
        default=None,
        description="Filter by projeto ID"
    ),
    categoria: CategoriaAnalise | None = Query(
        default=None,
        description="Filter by category"
    ),
    status_filter: AnaliseStatus | None = Query(
        default=None,
        alias="status",
        description="Filter by status"
    ),
    visibilidade: VisibilidadeAnalise | None = Query(
        default=None,
        description="Filter by visibility"
    ),
    search: str | None = Query(
        default=None,
        min_length=2,
        description="Search in titulo, descricao and termo_busca"
    ),
    favoritos_only: bool = Query(
        default=False,
        description="Only show análises with favorited fornecedores"
    ),
):
    """List análises with filters and pagination."""
    query: dict = {}

    query["$or"] = [
        {"created_by": user.sub},
        {"compartilhado_com": user.sub},
        {"visibilidade": VisibilidadeAnalise.PUBLICO.value},
    ]

    if projeto_id:
        query["projeto_id"] = projeto_id
    if categoria:
        query["categoria"] = categoria.value
    if status_filter:
        query["status"] = status_filter.value
    if visibilidade:
        query["visibilidade"] = visibilidade.value
    if search:
        query["$and"] = query.get("$and", []) + [{
            "$or": [
                {"titulo": {"$regex": search, "$options": "i"}},
                {"descricao": {"$regex": search, "$options": "i"}},
                {"termo_busca": {"$regex": search, "$options": "i"}},
            ]
        }]
    if favoritos_only:
        query["fornecedores.favorito"] = True

    total = await db[COLLECTION_NAME].count_documents(query)
    pages = (total + pagination.page_size - 1) // pagination.page_size if total > 0 else 1

    cursor = db[COLLECTION_NAME].find(query)
    cursor = cursor.sort("updated_at", -1)
    cursor = cursor.skip(pagination.skip).limit(pagination.page_size)

    items = []
    async for doc in cursor:
        doc = enrich_analise(doc)
        items.append(AnaliseListResponse(**doc))

    return PaginatedResponse(
        items=items,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
    )


@router.get(
    "/{analise_id}",
    response_model=AnaliseResponse,
    summary="Get análise by ID",
    description="Get detailed information about a specific análise."
)
async def get_analise(
    analise_id: str,
    db: Database,
    user: AuthenticatedUser,
):
    """Get análise by ID."""
    analise = await get_analise_or_404(db, analise_id)
    analise = enrich_analise(analise)
    return AnaliseResponse(**analise)


@router.put(
    "/{analise_id}",
    response_model=AnaliseResponse,
    summary="Update análise",
    description="Update an existing análise."
)
async def update_analise(
    analise_id: str,
    update: AnaliseUpdate,
    db: Database,
    user: AuthenticatedUser,
):
    """Update an existing análise."""
    await get_analise_or_404(db, analise_id)

    update_data = update.model_dump(exclude_unset=True, exclude_none=True)

    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    update_data["updated_at"] = datetime.utcnow()

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one({"_id": oid}, {"$set": update_data})

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Análise updated",
        analise_id=analise_id,
        fields=list(update_data.keys()),
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


@router.delete(
    "/{analise_id}",
    response_model=SuccessResponse,
    summary="Delete análise",
    description="Delete an análise."
)
async def delete_analise(
    analise_id: str,
    db: Database,
    user: AuthenticatedUser,
):
    """Delete an análise."""
    analise = await get_analise_or_404(db, analise_id)

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].delete_one({"_id": oid})

    logger.info(
        "Análise deleted",
        analise_id=analise_id,
        titulo=analise.get("titulo"),
        user_id=user.sub,
    )

    return SuccessResponse(message="Análise deleted successfully")


# =============================================================================
# Fornecedores Management (Generalizado)
# =============================================================================

@router.post(
    "/{analise_id}/fornecedores",
    response_model=AnaliseResponse,
    summary="Add fornecedor to análise",
    description="Add a fornecedor (jazida or empresa) to an análise."
)
async def add_fornecedor(
    analise_id: str,
    fornecedor: AddFornecedorRequest,
    db: Database,
    user: AuthenticatedUser,
):
    """Add a fornecedor to an análise."""
    analise = await get_analise_or_404(db, analise_id)

    existing = next(
        (f for f in analise.get("fornecedores", [])
         if f.get("id") == fornecedor.id and f.get("tipo_fonte") == fornecedor.tipo_fonte.value),
        None
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Fornecedor {fornecedor.id} ({fornecedor.tipo_fonte.value}) already in análise"
        )

    fornecedor_doc = fornecedor.model_dump()
    logger.info(
        "add_fornecedor payload",
        localizacao=fornecedor_doc.get("localizacao"),
        favorito=fornecedor_doc.get("favorito"),
        id=fornecedor_doc.get("id"),
    )
    fornecedor_doc["tipo_fonte"] = fornecedor.tipo_fonte.value
    fornecedor_doc["aprovado"] = None
    if fornecedor_doc.get("distancia_km") is None:
        fornecedor_doc["distancia_km"] = None
    fornecedor_doc["tempo_estimado_min"] = None
    fornecedor_doc["custo_frete_estimado"] = None
    fornecedor_doc["adicionado_em"] = datetime.utcnow()
    fornecedor_doc["adicionado_por"] = user.sub

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one(
        {"_id": oid},
        {
            "$push": {"fornecedores": fornecedor_doc},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Fornecedor added to análise",
        analise_id=analise_id,
        fornecedor_id=fornecedor.id,
        tipo_fonte=fornecedor.tipo_fonte.value,
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


@router.put(
    "/{analise_id}/fornecedores/{fornecedor_id}",
    response_model=AnaliseResponse,
    summary="Update fornecedor in análise",
    description="Update a fornecedor (favorito, aprovado, notas, contato)."
)
async def update_fornecedor(
    analise_id: str,
    fornecedor_id: str,
    update: UpdateFornecedorRequest,
    db: Database,
    user: AuthenticatedUser,
    tipo_fonte: TipoFonte = Query(
        default=TipoFonte.ANM,
        description="Tipo de fonte do fornecedor (anm, cnpj, manual)"
    ),
):
    """Update a fornecedor in an análise."""
    analise = await get_analise_or_404(db, analise_id)

    fornecedores = analise.get("fornecedores", [])
    fornecedor_index = next(
        (i for i, f in enumerate(fornecedores)
         if f.get("id") == fornecedor_id and f.get("tipo_fonte") == tipo_fonte.value),
        None
    )
    if fornecedor_index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Fornecedor {fornecedor_id} ({tipo_fonte.value}) not found in análise"
        )

    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update"
        )

    set_ops = {"updated_at": datetime.utcnow()}
    for key, value in update_data.items():
        set_ops[f"fornecedores.{fornecedor_index}.{key}"] = value

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one({"_id": oid}, {"$set": set_ops})

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Fornecedor updated in análise",
        analise_id=analise_id,
        fornecedor_id=fornecedor_id,
        tipo_fonte=tipo_fonte.value,
        fields=list(update_data.keys()),
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


@router.delete(
    "/{analise_id}/fornecedores/{fornecedor_id}",
    response_model=AnaliseResponse,
    summary="Remove fornecedor from análise",
    description="Remove a fornecedor from an análise."
)
async def remove_fornecedor(
    analise_id: str,
    fornecedor_id: str,
    db: Database,
    user: AuthenticatedUser,
    tipo_fonte: TipoFonte = Query(
        default=TipoFonte.ANM,
        description="Tipo de fonte do fornecedor (anm, cnpj, manual)"
    ),
):
    """Remove a fornecedor from an análise."""
    await get_analise_or_404(db, analise_id)

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one(
        {"_id": oid},
        {
            "$pull": {"fornecedores": {"id": fornecedor_id, "tipo_fonte": tipo_fonte.value}},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Fornecedor removed from análise",
        analise_id=analise_id,
        fornecedor_id=fornecedor_id,
        tipo_fonte=tipo_fonte.value,
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


# =============================================================================
# Bulk Operations
# =============================================================================

@router.post(
    "/{analise_id}/fornecedores/bulk",
    response_model=AnaliseResponse,
    summary="Add multiple fornecedores",
    description="Add multiple fornecedores to an análise at once."
)
async def add_fornecedores_bulk(
    analise_id: str,
    fornecedores: list[AddFornecedorRequest],
    db: Database,
    user: AuthenticatedUser,
):
    """Add multiple fornecedores to an análise."""
    analise = await get_analise_or_404(db, analise_id)

    existing_ids = {
        (f.get("id"), f.get("tipo_fonte"))
        for f in analise.get("fornecedores", [])
    }

    now = datetime.utcnow()
    new_fornecedores = []
    skipped = 0

    for fornecedor in fornecedores:
        key = (fornecedor.id, fornecedor.tipo_fonte.value)
        if key in existing_ids:
            skipped += 1
            continue

        fornecedor_doc = fornecedor.model_dump()
        fornecedor_doc["tipo_fonte"] = fornecedor.tipo_fonte.value
        fornecedor_doc["favorito"] = False
        fornecedor_doc["aprovado"] = None
        fornecedor_doc["distancia_km"] = None
        fornecedor_doc["tempo_estimado_min"] = None
        fornecedor_doc["custo_frete_estimado"] = None
        fornecedor_doc["adicionado_em"] = now
        fornecedor_doc["adicionado_por"] = user.sub

        new_fornecedores.append(fornecedor_doc)
        existing_ids.add(key)

    if new_fornecedores:
        oid = validate_object_id(analise_id)
        await db[COLLECTION_NAME].update_one(
            {"_id": oid},
            {
                "$push": {"fornecedores": {"$each": new_fornecedores}},
                "$set": {"updated_at": now},
            },
        )

    updated = await db[COLLECTION_NAME].find_one(
        {"_id": validate_object_id(analise_id)}
    )
    updated = enrich_analise(updated)

    logger.info(
        "Bulk fornecedores added to análise",
        analise_id=analise_id,
        added=len(new_fornecedores),
        skipped=skipped,
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


# =============================================================================
# Sharing
# =============================================================================

@router.post(
    "/{analise_id}/share",
    response_model=AnaliseResponse,
    summary="Share análise",
    description="Share análise with other users."
)
async def share_analise(
    analise_id: str,
    user_ids: list[str],
    db: Database,
    user: AuthenticatedUser,
):
    """Share análise with other users."""
    analise = await get_analise_or_404(db, analise_id)

    if analise.get("created_by") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can share this análise"
        )

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one(
        {"_id": oid},
        {
            "$addToSet": {"compartilhado_com": {"$each": user_ids}},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Análise shared",
        analise_id=analise_id,
        shared_with=user_ids,
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


@router.delete(
    "/{analise_id}/share/{target_user_id}",
    response_model=AnaliseResponse,
    summary="Unshare análise",
    description="Remove user access to análise."
)
async def unshare_analise(
    analise_id: str,
    target_user_id: str,
    db: Database,
    user: AuthenticatedUser,
):
    """Remove user access to análise."""
    analise = await get_analise_or_404(db, analise_id)

    if analise.get("created_by") != user.sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the creator can modify sharing settings"
        )

    oid = validate_object_id(analise_id)
    await db[COLLECTION_NAME].update_one(
        {"_id": oid},
        {
            "$pull": {"compartilhado_com": target_user_id},
            "$set": {"updated_at": datetime.utcnow()},
        },
    )

    updated = await db[COLLECTION_NAME].find_one({"_id": oid})
    updated = enrich_analise(updated)

    logger.info(
        "Análise unshared",
        analise_id=analise_id,
        removed_user=target_user_id,
        user_id=user.sub,
    )

    return AnaliseResponse(**updated)


# =============================================================================
# Duplicate
# =============================================================================

@router.post(
    "/{analise_id}/duplicate",
    response_model=AnaliseResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate análise",
    description="Create a copy of an existing análise."
)
async def duplicate_analise(
    analise_id: str,
    db: Database,
    user: AuthenticatedUser,
    target_projeto_id: str | None = Query(
        default=None,
        description="Target projeto ID (default: same projeto)"
    ),
):
    """Duplicate an análise."""
    original = await get_analise_or_404(db, analise_id)

    projeto_id = target_projeto_id or original["projeto_id"]
    await verify_projeto_exists(db, projeto_id)

    now = datetime.utcnow()

    copy = dict(original)
    del copy["_id"]
    copy["titulo"] = f"{copy['titulo']} (cópia)"
    copy["projeto_id"] = projeto_id
    copy["status"] = AnaliseStatus.RASCUNHO.value
    copy["compartilhado_com"] = []
    copy["created_by"] = user.sub
    copy["created_at"] = now
    copy["updated_at"] = now

    result = await db[COLLECTION_NAME].insert_one(copy)
    created = await db[COLLECTION_NAME].find_one({"_id": result.inserted_id})
    created = enrich_analise(created)

    logger.info(
        "Análise duplicated",
        original_id=analise_id,
        new_id=str(result.inserted_id),
        target_projeto_id=projeto_id,
        user_id=user.sub,
    )

    return AnaliseResponse(**created)
