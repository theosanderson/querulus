"""Main FastAPI application"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from querulus.config import config
from querulus.database import init_db, close_db, get_db, health_check
from querulus.query_builder import QueryBuilder
from querulus.compression import CompressionService

logger = logging.getLogger(__name__)


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

# Add CORS middleware to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    db_healthy, error = await health_check()
    if error:
        logger.error(f"Database health check failed: {error}")
    return {
        "status": "healthy" if db_healthy else "unhealthy",
        "database": "connected" if db_healthy else "disconnected",
    }


@app.get("/ready")
async def ready():
    """Readiness check for Kubernetes"""
    db_healthy, error = await health_check()
    if not db_healthy:
        logger.error(f"Database readiness check failed: {error}")
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
    dataFormat: str = Query("JSON", description="Output format: JSON or TSV"),
):
    """
    Get aggregated sequence counts with optional grouping by metadata fields.

    Examples:
    - GET /west-nile/sample/aggregated - Total count
    - GET /west-nile/sample/aggregated?fields=geoLocCountry - Group by country
    - GET /west-nile/sample/aggregated?geoLocCountry=USA - Filter by country
    - GET /west-nile/sample/aggregated?fields=geoLocCountry&geoLocCountry=USA - Both
    - GET /west-nile/sample/aggregated?fields=geoLocCountry&dataFormat=tsv - TSV format
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
                row_dict = {}
                for field in group_by_fields:
                    # Use _mapping to access column by name (handles camelCase)
                    row_dict[field] = row._mapping[field]
                row_dict["count"] = row.count
                data.append(row_dict)
        else:
            # Simple total count
            data = [{"count": rows[0].count if rows else 0}]

        # Return based on dataFormat
        if dataFormat.upper() == "TSV":
            # Generate TSV output
            if not data:
                return Response(content="", media_type="text/tab-separated-values")

            # Get column names (fields + count)
            columns = []
            if group_by_fields:
                columns.extend(group_by_fields)
            columns.append("count")

            # Build TSV
            tsv_lines = ["\t".join(columns)]
            for row_dict in data:
                row_values = []
                for col in columns:
                    value = row_dict.get(col, "")
                    # Convert None to empty string, otherwise convert to string
                    row_values.append("" if value is None else str(value))
                tsv_lines.append("\t".join(row_values))

            tsv_content = "\n".join(tsv_lines)
            return Response(content=tsv_content, media_type="text/tab-separated-values")
        else:
            # Return JSON format
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


@app.post("/{organism}/sample/aggregated")
async def post_aggregated(organism: str, body: dict = {}):
    """POST version of aggregated endpoint - accepts JSON body with query parameters."""
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Convert body to QueryBuilder format
    fields_list = body.get("fields", [])
    group_by_fields = fields_list if isinstance(fields_list, list) else []

    limit = body.get("limit")
    offset = body.get("offset", 0)

    # Parse orderBy - can be array of strings or array of {field, type} objects
    order_by_raw = body.get("orderBy", [])
    order_by_fields = []
    if isinstance(order_by_raw, list):
        for item in order_by_raw:
            if isinstance(item, dict) and "field" in item:
                # Format: {field: "date", type: "descending"}
                field = item["field"]
                direction = item.get("type", "ascending")  # Default to ascending
                order_by_fields.append((field, direction))
            elif isinstance(item, str):
                order_by_fields.append(item)  # String means ascending by default
    elif isinstance(order_by_raw, str):
        order_by_fields = [order_by_raw]

    # Build query
    builder = QueryBuilder(organism, organism_config)
    builder.set_group_by_fields(group_by_fields)
    builder.set_order_by_fields(order_by_fields)

    # Add filters (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["fields", "limit", "offset", "orderBy",
                    "nucleotideMutations", "aminoAcidMutations",
                    "nucleotideInsertions", "aminoAcidInsertions"]:
            filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Execute query
    async for db in get_db():
        query_str, params = builder.build_aggregated_query(limit, offset)
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Format results
        data = []
        if group_by_fields:
            for row in rows:
                row_dict = {"count": row.count}
                for field in group_by_fields:
                    row_dict[field] = row._mapping[field]
                data.append(row_dict)
        else:
            data = [{"count": rows[0].count if rows else 0}]

        return {
            "data": data,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
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
    dataFormat: str = Query("JSON", description="Output format: JSON or TSV"),
):
    """
    Get detailed metadata for sequences.

    Examples:
    - GET /west-nile/sample/details?limit=10
    - GET /west-nile/sample/details?fields=accession,geoLocCountry,lineage&limit=5
    - GET /west-nile/sample/details?geoLocCountry=USA&limit=10
    - GET /west-nile/sample/details?limit=10&dataFormat=tsv
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
        # Debug logging
        if "versionStatus" in query_params:
            print(f"\n=== DEBUG: Details query with versionStatus filter ===")
            print(f"Query:\n{query_str}")
            print(f"Params: {params}")
            print("=" * 60)
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

        # Return based on dataFormat
        if dataFormat.upper() == "TSV":
            # Generate TSV output
            if not data:
                return Response(content="", media_type="text/tab-separated-values")

            # Get all column names from first row
            columns = list(data[0].keys())

            # Build TSV
            tsv_lines = ["\t".join(columns)]
            for row_dict in data:
                row_values = []
                for col in columns:
                    value = row_dict.get(col, "")
                    # Convert None to empty string, handle special types
                    if value is None:
                        row_values.append("")
                    elif isinstance(value, dict) or isinstance(value, list):
                        # Convert complex types to JSON string
                        import json
                        row_values.append(json.dumps(value))
                    else:
                        row_values.append(str(value))
                tsv_lines.append("\t".join(row_values))

            tsv_content = "\n".join(tsv_lines)
            return Response(content=tsv_content, media_type="text/tab-separated-values")
        else:
            # Return JSON format
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


@app.post("/{organism}/sample/details")
async def post_details(organism: str, body: dict = {}):
    """POST version of details endpoint - accepts JSON body with query parameters."""
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Extract parameters from body
    fields_list = body.get("fields", [])
    selected_fields = fields_list if isinstance(fields_list, list) and fields_list else None

    limit = body.get("limit")
    offset = body.get("offset", 0)

    # Parse orderBy - can be array of strings or array of {field, type} objects
    order_by_raw = body.get("orderBy", [])
    order_by_fields = []
    if isinstance(order_by_raw, list):
        for item in order_by_raw:
            if isinstance(item, dict) and "field" in item:
                # Format: {field: "date", type: "descending"}
                field = item["field"]
                direction = item.get("type", "ascending")  # Default to ascending
                order_by_fields.append((field, direction))
            elif isinstance(item, str):
                order_by_fields.append(item)  # String means ascending by default
    elif isinstance(order_by_raw, str):
        order_by_fields = [order_by_raw]

    # Build query
    builder = QueryBuilder(organism, organism_config)
    builder.set_order_by_fields(order_by_fields)

    # Add filters (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["fields", "limit", "offset", "orderBy",
                    "nucleotideMutations", "aminoAcidMutations",
                    "nucleotideInsertions", "aminoAcidInsertions"]:
            filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Execute query
    async for db in get_db():
        query_str, params = builder.build_details_query(selected_fields, limit, offset)
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Format results
        data = [dict(row._mapping) for row in rows]

        return {
            "data": data,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
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
    dataFormat: str = Query("FASTA", description="Output format: FASTA or JSON"),
):
    """
    Get aligned nucleotide sequences in FASTA or JSON format.

    Examples:
    - GET /west-nile/sample/alignedNucleotideSequences?limit=10
    - GET /west-nile/sample/alignedNucleotideSequences?geoLocCountry=USA&limit=5
    - GET /west-nile/sample/alignedNucleotideSequences?limit=5&dataFormat=JSON
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

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if dataFormat.upper() == "JSON":
            # Return JSON array with accessionVersion and main (segment name)
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    "main": seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


@app.get("/{organism}/sample/unalignedNucleotideSequences")
async def get_unaligned_nucleotide_sequences(
    organism: str,
    request: Request,
    limit: int | None = Query(None, description="Maximum number of sequences"),
    offset: int = Query(0, description="Number of sequences to skip"),
    dataFormat: str = Query("FASTA", description="Output format: FASTA or JSON"),
    downloadAsFile: bool = Query(False, description="Trigger file download"),
    downloadFileBasename: str | None = Query(None, description="Basename for downloaded file"),
):
    """
    Get unaligned nucleotide sequences in FASTA or JSON format.

    Examples:
    - GET /west-nile/sample/unalignedNucleotideSequences?limit=10
    - GET /west-nile/sample/unalignedNucleotideSequences?geoLocCountry=USA&limit=5
    - GET /west-nile/sample/unalignedNucleotideSequences?limit=5&dataFormat=JSON
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

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if dataFormat.upper() == "JSON":
            # Return JSON array with accessionVersion and main (segment name)
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    "main": seq["sequence"]
                }
                for seq in sequences
            ]
            response = JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            response = Response(content=fasta_content, media_type="text/x-fasta")

        # Add Content-Disposition header if downloadAsFile is true
        if downloadAsFile:
            filename = downloadFileBasename if downloadFileBasename else f"{organism}_sequences"
            # Add appropriate extension based on format
            if dataFormat.upper() == "JSON":
                filename += ".json"
            else:
                filename += ".fasta"
            response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'

        return response


@app.post("/{organism}/sample/unalignedNucleotideSequences")
async def post_unaligned_nucleotide_sequences(organism: str, request: Request, body: dict = {}):
    """POST version of unaligned nucleotide sequences endpoint - accepts JSON body with query parameters."""
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Extract parameters from body
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)

    # Add filters (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["limit", "offset", "dataFormat"]:
            filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Build sequences query
    query_str, params = builder.build_unaligned_sequences_query("main", limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if data_format.upper() == "JSON":
            # Return JSON array with accessionVersion and main (segment name)
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    "main": seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


@app.post("/{organism}/sample/unalignedNucleotideSequences/{segment}")
async def post_unaligned_nucleotide_sequences_segment(organism: str, segment: str, request: Request, body: dict = {}):
    """POST version of unaligned nucleotide sequences endpoint with segment parameter."""
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Extract parameters from body
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)

    # Add filters (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["limit", "offset", "dataFormat"]:
            filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Build sequences query for the specified segment
    query_str, params = builder.build_unaligned_sequences_query(segment, limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

        for row in rows:
            accession_version = f"{row.accession}.{row.version}"
            compressed_seq = row.compressed_seq

            if not compressed_seq:
                continue

            try:
                # Decompress sequence
                sequence = compression.decompress_nucleotide_sequence(
                    compressed_seq, organism, segment
                )
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if data_format.upper() == "JSON":
            # Return JSON array with accessionVersion and segment name
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    segment: seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


@app.post("/{organism}/sample/alignedNucleotideSequences")
async def post_aligned_nucleotide_sequences(organism: str, request: Request, body: dict = {}):
    """POST version of aligned nucleotide sequences endpoint - accepts JSON body with query parameters."""
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Extract parameters from body
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)

    # Add filters (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["limit", "offset", "dataFormat"]:
            filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Build sequences query
    query_str, params = builder.build_sequences_query("main", limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if data_format.upper() == "JSON":
            # Return JSON array with accessionVersion and main (segment name)
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    "main": seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


@app.get("/{organism}/sample/alignedAminoAcidSequences/{gene}")
async def get_aligned_amino_acid_sequences(
    organism: str,
    gene: str,
    request: Request,
    limit: int | None = Query(None, description="Maximum number of sequences"),
    offset: int = Query(0, description="Number of sequences to skip"),
    dataFormat: str = Query("FASTA", description="Output format: FASTA or JSON"),
):
    """
    Get aligned amino acid sequences in FASTA or JSON format.

    Examples:
    - GET /west-nile/sample/alignedAminoAcidSequences/2K?limit=10
    - GET /west-nile/sample/alignedAminoAcidSequences/2K?geoLocCountry=USA&limit=5
    - GET /west-nile/sample/alignedAminoAcidSequences/2K?limit=5&dataFormat=JSON
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

    # Build amino acid sequences query
    query_str, params = builder.build_amino_acid_sequences_query(gene, limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if dataFormat.upper() == "JSON":
            # Return JSON array with accessionVersion and gene name
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    gene: seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


@app.post("/{organism}/sample/alignedAminoAcidSequences/{gene}")
async def post_aligned_amino_acid_sequences(
    organism: str,
    gene: str,
    request: Request,
    body: dict = Body(...),
):
    """
    POST endpoint for aligned amino acid sequences.
    Accepts JSON body with query parameters including filters, limit, offset, and dataFormat.

    Examples:
    - POST /ebola-sudan/sample/alignedAminoAcidSequences/VP35
      Body: {"accessionVersion": "LOC_00004T9.1", "dataFormat": "FASTA"}
    """
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Extract limit, offset, dataFormat from body
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")

    # Build query using QueryBuilder
    builder = QueryBuilder(organism, organism_config)
    builder.add_filters_from_params(body)

    # Build amino acid sequences query
    query_str, params = builder.build_amino_acid_sequences_query(gene, limit, offset)

    # Execute query
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Decompress sequences
        compression = request.app.state.compression
        sequences = []

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
                sequences.append({
                    "accessionVersion": accession_version,
                    "sequence": sequence
                })
            except Exception as e:
                print(f"Error decompressing {accession_version}: {e}")
                continue

        # Return based on dataFormat
        if data_format.upper() == "JSON":
            # Return JSON array with accessionVersion and gene name
            json_data = [
                {
                    "accessionVersion": seq["accessionVersion"],
                    gene: seq["sequence"]
                }
                for seq in sequences
            ]
            return JSONResponse(content=json_data)
        else:
            # Return FASTA format
            fasta_lines = []
            for seq in sequences:
                fasta_lines.append(f">{seq['accessionVersion']}")
                fasta_lines.append(seq["sequence"])
            fasta_content = "\n".join(fasta_lines)
            return Response(content=fasta_content, media_type="text/x-fasta")


# ===== MUTATION ENDPOINTS =====


@app.post("/{organism}/sample/nucleotideInsertions")
async def post_nucleotide_insertions(organism: str, body: dict = {}):
    """
    Get nucleotide insertions aggregated across all matching sequences.

    Returns list of insertions with counts, positions, and inserted symbols.
    """
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query to get all insertions from matching sequences
    builder = QueryBuilder(organism, organism_config)

    # Add filters from body (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["fields", "limit", "offset", "orderBy",
                    "nucleotideMutations", "aminoAcidMutations",
                    "nucleotideInsertions", "aminoAcidInsertions"]:
            if k == "isRevocation":
                filter_params["is_revocation"] = v if isinstance(v, bool) else v.lower() == "true"
            else:
                filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Build query to get insertions
    # We need to expand the insertions array and count occurrences
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

    # Query to aggregate insertions
    # The insertions are stored as {"main": ["position:sequence", ...]}
    query_str = f"""
        WITH insertions_data AS (
            SELECT
                jsonb_array_elements_text(joint_metadata -> 'nucleotideInsertions' -> 'main') as insertion_str
            FROM sequence_entries_view
            WHERE organism = :organism
              AND released_at IS NOT NULL
              AND joint_metadata -> 'nucleotideInsertions' -> 'main' IS NOT NULL
              {where_clause}
        ),
        parsed_insertions AS (
            SELECT
                split_part(insertion_str, ':', 1)::int as position,
                split_part(insertion_str, ':', 2) as inserted_symbols
            FROM insertions_data
        )
        SELECT
            'ins_' || position || ':' || inserted_symbols as insertion,
            COUNT(*) as count,
            inserted_symbols,
            position,
            NULL::text as sequence_name
        FROM parsed_insertions
        GROUP BY position, inserted_symbols
        ORDER BY count DESC, position ASC
    """

    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Format results
        data = []
        for row in rows:
            data.append({
                "insertion": row.insertion,
                "count": row.count,
                "insertedSymbols": row.inserted_symbols,
                "position": row.position,
                "sequenceName": row.sequence_name
            })

        return {
            "data": data,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Nucleotide insertions query"
            }
        }


@app.post("/{organism}/sample/aminoAcidInsertions")
async def post_amino_acid_insertions(organism: str, body: dict = {}):
    """
    Get amino acid insertions aggregated across all matching sequences.

    Returns list of insertions with counts, positions, gene names, and inserted symbols.
    """
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query to get all insertions from matching sequences
    builder = QueryBuilder(organism, organism_config)

    # Add filters from body (excluding special fields)
    filter_params = {}
    for k, v in body.items():
        if k not in ["fields", "limit", "offset", "orderBy",
                    "nucleotideMutations", "aminoAcidMutations",
                    "nucleotideInsertions", "aminoAcidInsertions"]:
            if k == "isRevocation":
                filter_params["is_revocation"] = v if isinstance(v, bool) else v.lower() == "true"
            else:
                filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Build query to get insertions from all genes
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

    # Query to aggregate amino acid insertions across all genes
    # The insertions are stored as {"gene1": ["position:sequence", ...], "gene2": [...]}
    query_str = f"""
        WITH gene_insertions AS (
            SELECT
                gene_key as gene,
                jsonb_array_elements_text(gene_insertions) as insertion_str
            FROM sequence_entries_view,
                 LATERAL jsonb_each(joint_metadata -> 'aminoAcidInsertions') as genes(gene_key, gene_insertions)
            WHERE organism = :organism
              AND released_at IS NOT NULL
              AND joint_metadata -> 'aminoAcidInsertions' IS NOT NULL
              AND jsonb_typeof(gene_insertions) = 'array'
              {where_clause}
        ),
        parsed_insertions AS (
            SELECT
                gene,
                split_part(insertion_str, ':', 1)::int as position,
                split_part(insertion_str, ':', 2) as inserted_symbols
            FROM gene_insertions
        )
        SELECT
            'ins_' || gene || ':' || position || ':' || inserted_symbols as insertion,
            COUNT(*) as count,
            inserted_symbols,
            position,
            gene as sequence_name
        FROM parsed_insertions
        GROUP BY gene, position, inserted_symbols
        ORDER BY count DESC, gene ASC, position ASC
    """

    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Format results
        data = []
        for row in rows:
            data.append({
                "insertion": row.insertion,
                "count": row.count,
                "insertedSymbols": row.inserted_symbols,
                "position": row.position,
                "sequenceName": row.sequence_name
            })

        return {
            "data": data,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Amino acid insertions query"
            }
        }


@app.get("/{organism}/sample/nucleotideMutations")
async def get_nucleotide_mutations(
    organism: str,
    request: Request,
):
    """Get nucleotide mutations for matching sequences"""
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query using QueryBuilder for proper filter handling
    builder = QueryBuilder(organism, organism_config)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Use the new aligned sequences metadata query
    query_str, params = builder.build_aligned_sequences_metadata_query(limit=None, offset=0)

    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Calculate mutations for each sequence
        all_mutations = []
        compression = request.app.state.compression

        for row in rows:
            # Get nucleotide sequences
            aligned_sequences = row.aligned_sequences or {}

            for segment_name, seq_data in aligned_sequences.items():
                if not seq_data or 'compressedSequence' not in seq_data:
                    continue

                # Decompress sequence
                try:
                    sequence = compression.decompress_nucleotide_sequence(
                        seq_data['compressedSequence'],
                        organism,
                        segment_name
                    )

                    # Get reference sequence to compare
                    reference_seq = organism_config.referenceGenome.get_nucleotide_sequence(segment_name)
                    if not reference_seq:
                        continue

                    # Calculate mutations by comparing to reference
                    for i, (ref_base, seq_base) in enumerate(zip(reference_seq, sequence)):
                        if ref_base != seq_base and seq_base != 'N':  # Ignore N's (unknown)
                            position = i + 1  # 1-indexed
                            all_mutations.append({
                                "mutation": f"{ref_base}{position}{seq_base}",
                                "mutationFrom": ref_base,
                                "mutationTo": seq_base,
                                "position": position,
                                "sequenceName": None,  # nucleotide mutations don't have sequence name
                                "count": 1,
                                "coverage": 1,
                                "proportion": 1.0
                            })

                except Exception as e:
                    logger.error(f"Error calculating mutations for {row.accession}.{row.version}: {e}")
                    continue

        return {
            "data": all_mutations,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Nucleotide mutations query"
            }
        }


@app.post("/{organism}/sample/nucleotideMutations")
async def post_nucleotide_mutations(
    organism: str,
    request: Request,
    body: dict = Body({}),
):
    """POST version of nucleotide mutations endpoint"""
    # Merge query params and body params
    params_dict = dict(request.query_params)
    if body:
        params_dict.update(body)

    # Build new request with combined params
    class FakeRequest:
        def __init__(self, params, app_state):
            self.query_params = params
            self.app = type('obj', (object,), {'state': app_state})()

    fake_request = FakeRequest(params_dict, request.app.state)
    return await get_nucleotide_mutations(organism, fake_request)


@app.get("/{organism}/sample/aminoAcidMutations")
async def get_amino_acid_mutations(
    organism: str,
    request: Request,
):
    """Get amino acid mutations for matching sequences"""
    # Validate organism
    try:
        organism_config = config.get_organism_config(organism)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    # Build query using QueryBuilder for proper filter handling
    builder = QueryBuilder(organism, organism_config)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    # Use the new aligned sequences metadata query
    query_str, params = builder.build_aligned_sequences_metadata_query(limit=None, offset=0)

    async for db in get_db():
        result = await db.execute(text(query_str), params)
        rows = result.fetchall()

        # Calculate mutations for each sequence
        all_mutations = []
        compression = request.app.state.compression

        for row in rows:
            # Get amino acid sequences
            aa_sequences = row.amino_acid_sequences or {}

            for gene_name, seq_data in aa_sequences.items():
                if not seq_data or 'compressedSequence' not in seq_data:
                    continue

                # Decompress sequence
                try:
                    sequence = compression.decompress_amino_acid_sequence(
                        seq_data['compressedSequence'],
                        organism,
                        gene_name
                    )

                    # Get reference sequence to compare
                    reference_seq = organism_config.referenceGenome.get_gene_sequence(gene_name)
                    if not reference_seq:
                        continue

                    # Calculate mutations by comparing to reference
                    for i, (ref_aa, seq_aa) in enumerate(zip(reference_seq, sequence)):
                        if ref_aa != seq_aa and seq_aa != 'X':  # Ignore X's (unknown)
                            mutation_str = f"{gene_name}:{ref_aa}{i+1}{seq_aa}"
                            all_mutations.append({
                                "mutation": mutation_str,
                                "mutationFrom": ref_aa,
                                "mutationTo": seq_aa,
                                "position": i + 1,
                                "count": 1,
                                "coverage": 1,
                                "proportion": 1.0,
                                "sequenceName": gene_name
                            })
                except Exception as e:
                    logger.error(f"Error processing {gene_name} for {row.accession}.{row.version}: {e}")
                    continue

        return {
            "data": all_mutations,
            "info": {
                "dataVersion": "0",
                "requestId": str(uuid.uuid4()),
                "requestInfo": f"{organism_config.schema['organismName']} on querulus",
                "queryInfo": "Amino acid mutations query"
            }
        }


@app.post("/{organism}/sample/aminoAcidMutations")
async def post_amino_acid_mutations(
    organism: str,
    request: Request,
    body: dict = Body({}),
):
    """POST version of amino acid mutations endpoint"""
    # Merge query params and body params
    params_dict = dict(request.query_params)
    if body:
        params_dict.update(body)

    # Build new request with combined params
    class FakeRequest:
        def __init__(self, params, app_state):
            self.query_params = params
            self.app = type('obj', (object,), {'state': app_state})()

    fake_request = FakeRequest(params_dict, request.app.state)
    return await get_amino_acid_mutations(organism, fake_request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
