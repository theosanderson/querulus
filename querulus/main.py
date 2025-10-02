"""Main FastAPI application"""

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from querulus.config import config
from querulus.database import init_db, close_db, get_db, health_check
from querulus.query_builder import QueryBuilder
from querulus.compression import CompressionService


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

    # Initialize compression service
    app.state.compression = CompressionService(config.backend_config)
    print("Compression service initialized")

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
async def get_aggregated(
    organism: str,
    request: Request,
    fields: str | None = Query(None, description="Comma-separated list of fields to group by"),
    limit: int | None = Query(None, description="Maximum number of results"),
    offset: int = Query(0, description="Number of results to skip"),
):
    """
    Get aggregated sequence counts with optional grouping by metadata fields.

    Examples:
    - GET /west-nile/sample/aggregated - Total count
    - GET /west-nile/sample/aggregated?fields=geoLocCountry - Group by country
    - GET /west-nile/sample/aggregated?geoLocCountry=USA - Filter by country
    - GET /west-nile/sample/aggregated?fields=geoLocCountry&geoLocCountry=USA - Both
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Parse fields parameter
    group_by_fields = []
    if fields:
        group_by_fields = [f.strip() for f in fields.split(",")]

    # Parse orderBy parameter (can appear multiple times)
    order_by_fields = request.query_params.getlist("orderBy") if hasattr(request.query_params, "getlist") else []
    if not order_by_fields and "orderBy" in request.query_params:
        order_by_fields = [request.query_params["orderBy"]]

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)
    builder.set_group_by_fields(group_by_fields)
    builder.set_order_by_fields(order_by_fields)

    # Add filters from query parameters
    # Extract all query params except special ones
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Get database session
    async for db in get_db():
        # Build and execute query
        query_str, params = builder.build_aggregated_query(limit, offset)
        result = await db.execute(text(query_str), params)

        # Format results
        rows = result.fetchall()
        data = []

        if group_by_fields:
            # Return grouped results with field names
            for row in rows:
                row_dict = {"count": row.count}
                for field in group_by_fields:
                    # Use _mapping to access column by name (handles camelCase)
                    row_dict[field] = row._mapping[field]
                data.append(row_dict)
        else:
            # Simple total count
            data = [{"count": rows[0].count if rows else 0}]

        # Generate request ID
        request_id = str(uuid.uuid4())

        # Return LAPIS-compatible response
        return {
            "data": data,
            "info": {
                "dataVersion": "0",  # TODO: Implement versioning
                "requestId": request_id,
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Aggregated query",
            },
        }


@app.get("/{organism}/sample/details")
async def get_details(
    organism: str,
    request: Request,
    fields: str | None = Query(None, description="Comma-separated list of fields to return"),
    limit: int | None = Query(None, description="Maximum number of results"),
    offset: int = Query(0, description="Number of results to skip"),
):
    """
    Get detailed metadata for sequences.

    Examples:
    - GET /west-nile/sample/details?limit=10
    - GET /west-nile/sample/details?fields=accession,geoLocCountry,lineage&limit=5
    - GET /west-nile/sample/details?geoLocCountry=USA&limit=10
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Parse fields parameter
    selected_fields = None
    if fields:
        selected_fields = [f.strip() for f in fields.split(",")]

    # Parse orderBy parameter (can appear multiple times)
    order_by_fields = request.query_params.getlist("orderBy") if hasattr(request.query_params, "getlist") else []
    if not order_by_fields and "orderBy" in request.query_params:
        order_by_fields = [request.query_params["orderBy"]]

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)
    builder.set_order_by_fields(order_by_fields)

    # Add filters from query parameters
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Get database session
    async for db in get_db():
        # Build and execute query
        query_str, params = builder.build_details_query(selected_fields, limit, offset)
        result = await db.execute(text(query_str), params)

        # Format results
        rows = result.fetchall()
        data = []

        for row in rows:
            row_dict = {}
            # Get all columns from the row
            for key in row._mapping.keys():
                value = row._mapping[key]
                # Convert any non-serializable types
                if isinstance(value, dict):
                    row_dict[key] = value
                else:
                    row_dict[key] = value
            data.append(row_dict)

        # Generate request ID
        request_id = str(uuid.uuid4())

        # Return LAPIS-compatible response
        return {
            "data": data,
            "info": {
                "dataVersion": "0",  # TODO: Implement versioning
                "requestId": request_id,
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Details query",
            },
        }


@app.get("/{organism}/sample/alignedNucleotideSequences")
async def get_aligned_nucleotide_sequences(
    organism: str,
    request: Request,
    limit: int | None = Query(None, description="Maximum number of sequences"),
    offset: int = Query(0, description="Number of sequences to skip"),
):
    """
    Get aligned nucleotide sequences in FASTA format.

    Examples:
    - GET /west-nile/sample/alignedNucleotideSequences?limit=10
    - GET /west-nile/sample/alignedNucleotideSequences?geoLocCountry=USA&limit=5
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Build sequences query
    query_str, params = builder.build_sequences_query("main", limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences and format as FASTA
        fasta_lines = []
        compression = request.app.state.compression

        for row in rows:
            accession_version = f"{row.accession}.{row.version}"
            compressed_seq = row.compressed_seq

            if not compressed_seq:
                continue

            try:
                # Decompress sequence
                sequence = compression.decompress_nucleotide_sequence(
                    compressed_seq, organism, "main"
                )
                # Add FASTA header and sequence
                fasta_lines.append(f">{accession_version}")
                fasta_lines.append(sequence)
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return FASTA format
        fasta_content = "\n".join(fasta_lines)
        return Response(content=fasta_content, media_type="text/x-fasta")


@app.get("/{organism}/sample/unalignedNucleotideSequences")
async def get_unaligned_nucleotide_sequences(
    organism: str,
    request: Request,
    limit: int | None = Query(None, description="Maximum number of sequences"),
    offset: int = Query(0, description="Number of sequences to skip"),
):
    """
    Get unaligned nucleotide sequences in FASTA format.

    Examples:
    - GET /west-nile/sample/unalignedNucleotideSequences?limit=10
    - GET /west-nile/sample/unalignedNucleotideSequences?geoLocCountry=USA&limit=5
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Build sequences query - unaligned sequences are in unalignedNucleotideSequences
    query_str, params = builder.build_unaligned_sequences_query("main", limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences and format as FASTA
        fasta_lines = []
        compression = request.app.state.compression

        for row in rows:
            accession_version = f"{row.accession}.{row.version}"
            compressed_seq = row.compressed_seq

            if not compressed_seq:
                continue

            try:
                # Decompress sequence
                sequence = compression.decompress_nucleotide_sequence(
                    compressed_seq, organism, "main"
                )
                # Add FASTA header and sequence
                fasta_lines.append(f">{accession_version}")
                fasta_lines.append(sequence)
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return FASTA format
        fasta_content = "\n".join(fasta_lines)
        return Response(content=fasta_content, media_type="text/x-fasta")


@app.get("/{organism}/sample/alignedAminoAcidSequences/{gene}")
async def get_aligned_amino_acid_sequences(
    organism: str,
    gene: str,
    request: Request,
    limit: int | None = Query(None, description="Maximum number of sequences"),
    offset: int = Query(0, description="Number of sequences to skip"),
):
    """
    Get aligned amino acid sequences in FASTA format.

    Examples:
    - GET /west-nile/sample/alignedAminoAcidSequences/2K?limit=10
    - GET /west-nile/sample/alignedAminoAcidSequences/2K?geoLocCountry=USA&limit=5
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query using QueryBuilder - amino acid sequences are in alignedAminoAcidSequences
    builder = QueryBuilder(organism, organism_config)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Build sequences query for amino acid
    params = {"organism": organism}
    # Build WHERE clause for filters
    where_clauses = []
    for field, value in builder.filters.items():
        param_name = f"filter_{field}"
        where_clauses.append(f"joint_metadata -> 'metadata' ->> '{field}' = :{param_name}")
        params[param_name] = value

    where_clause = ""
    if where_clauses:
        where_clause = " AND " + " AND ".join(where_clauses)

    # Query for amino acid sequences
    query_str = f"""
        SELECT
            accession,
            version,
            joint_metadata -> 'alignedAminoAcidSequences' -> :gene ->> 'compressedSequence' as compressed_seq
        FROM sequence_entries_view
        WHERE organism = :organism
          AND released_at IS NOT NULL
          {where_clause}
        ORDER BY accession, version
    """

    if limit is not None:
        query_str += f" LIMIT {limit}"
    if offset > 0:
        query_str += f" OFFSET {offset}"

    params["gene"] = gene

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences and format as FASTA
        fasta_lines = []
        compression = request.app.state.compression

        for row in rows:
            accession_version = f"{row.accession}.{row.version}"
            compressed_seq = row.compressed_seq

            if not compressed_seq:
                continue

            try:
                # Decompress amino acid sequence
                sequence = compression.decompress_amino_acid_sequence(
                    compressed_seq, organism, gene
                )
                # Add FASTA header and sequence
                fasta_lines.append(f">{accession_version}")
                fasta_lines.append(sequence)
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return FASTA format
        fasta_content = "\n".join(fasta_lines)
        return Response(content=fasta_content, media_type="text/x-fasta")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
