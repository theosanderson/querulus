"""Main FastAPI application"""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from querulus.config import config
from querulus.database import init_db, close_db, get_db, health_check


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager for FastAPI app"""
    # Startup
    print("Starting Querulus...")

    # Load backend config with reference genomes
    config.load_backend_config()
    print(f"Loaded config for organisms: {', '.join(config.backend_config.organisms.keys())}")

    # Initialize database connection pool
    await init_db()
    print("Database connection pool initialized")

    yield

    # Shutdown
    print("Shutting down Querulus...")
    await close_db()


app = FastAPI(
    title="Querulus",
    description="Direct PostgreSQL-backed LAPIS API replacement",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "Querulus",
        "version": "0.1.0",
        "description": "Direct PostgreSQL-backed LAPIS API replacement",
        "organisms": list(config.backend_config.organisms.keys()) if config.backend_config else [],
    }


@app.get("/health")
async def health():
    """Health check endpoint"""
    db_healthy = await health_check()
    return {
        "status": "healthy" if db_healthy else "unhealthy",
        "database": "connected" if db_healthy else "disconnected",
    }


@app.get("/ready")
async def ready():
    """Readiness check for Kubernetes"""
    db_healthy = await health_check()
    if not db_healthy:
        return JSONResponse(
            status_code=503,
            content={"status": "not ready", "reason": "database not connected"},
        )
    return {"status": "ready"}


@app.get("/{organism}/sample/aggregated")
async def get_aggregated(organism: str, request: Request):
    """
    Get aggregated sequence counts

    This is a simple implementation that returns total count.
    Will be expanded to support grouping by fields.
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Get database session
    async for db in get_db():
        # Query total count of released sequences
        query = text("""
            SELECT COUNT(*) as count
            FROM sequence_entries_view
            WHERE organism = :organism
              AND released_at IS NOT NULL
        """)

        result = await db.execute(query, {"organism": organism})
        count = result.scalar()

        # Generate request ID
        request_id = str(uuid.uuid4())

        # Return LAPIS-compatible response
        return {
            "data": [{"count": count}],
            "info": {
                "dataVersion": "0",  # TODO: Implement versioning
                "requestId": request_id,
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Aggregated query",
            },
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
