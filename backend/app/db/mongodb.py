"""
MongoDB Connection
==================

Async MongoDB client using Motor driver.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MongoDBClient:
    """
    MongoDB client wrapper with connection lifecycle management.
    
    Usage:
        # At startup
        await mongodb_client.connect()
        
        # Get database
        db = mongodb_client.database
        collection = db["users"]
        
        # At shutdown
        await mongodb_client.disconnect()
    """

    def __init__(self):
        self.client: AsyncIOMotorClient | None = None
        self.database: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        """Establish connection to MongoDB."""
        self.client = AsyncIOMotorClient(
            settings.mongodb_connection_string,
            minPoolSize=settings.mongodb_min_pool_size,
            maxPoolSize=settings.mongodb_max_pool_size,
            serverSelectionTimeoutMS=5000,
        )

        # Set database immediately (Motor is lazy — no socket opened yet)
        self.database = self.client[settings.mongodb_database]

        # Verify connection with a non-fatal ping
        try:
            await self.client.admin.command("ping")
            logger.info(
                "MongoDB connected",
                database=settings.mongodb_database,
                pool_size=settings.mongodb_max_pool_size,
            )
        except Exception as e:
            logger.warning(
                "MongoDB ping failed at startup — will retry on first use",
                error=str(e),
            )

    async def disconnect(self) -> None:
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            self.client = None
            self.database = None
            logger.info("MongoDB disconnected")

    def get_collection(self, name: str):
        """Get a collection by name."""
        if self.database is None:
            raise RuntimeError("MongoDB not connected")
        return self.database[name]


# Singleton instance
mongodb_client = MongoDBClient()


async def get_database() -> AsyncIOMotorDatabase:
    """
    FastAPI dependency to get MongoDB database.
    
    Usage:
        @router.get("/users")
        async def get_users(db: Database):
            return await db["users"].find().to_list(100)
    """
    if mongodb_client.database is None:
        raise RuntimeError("MongoDB not connected")
    return mongodb_client.database


async def get_db_direct() -> AsyncIOMotorDatabase:
    """Get MongoDB database directly (outside FastAPI dependency injection).

    Useful for background tasks and LangGraph nodes that don't have
    access to FastAPI's Depends().
    """
    if mongodb_client.database is None:
        raise RuntimeError("MongoDB not connected")
    return mongodb_client.database
