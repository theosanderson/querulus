"""Main FastAPI application (refactored to reduce duplication)"""

import json
import logging
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from querulus.config import config
from querulus.database import init_db, close_db, get_db, health_check
from querulus.query_builder import QueryBuilder
from querulus.compression import CompressionService

logger = logging.getLogger(__name__)

# =============================================================================
# Lifespan
# =============================================================================


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

# =============================================================================
# Error handling & utilities
# =============================================================================


class OrganismNotFound(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@app.exception_handler(OrganismNotFound)
async def organism_not_found_handler(_request: Request, exc: OrganismNotFound):
    return JSONResponse(status_code=404, content={"error": exc.message})


def validate_organism_or_404(organism: str):
    try:
        return config.get_organism_config(organism)
    except ValueError as e:
        raise OrganismNotFound(str(e))


def parse_fields_param(fields: Optional[str]) -> List[str]:
    return [f.strip() for f in fields.split(",")] if fields else []


def parse_order_by_get(request: Request) -> List[str]:
    qp = request.query_params
    if hasattr(qp, "getlist"):
        order_by = qp.getlist("orderBy")
        if order_by:
            return order_by
    if "orderBy" in qp:
        return [qp["orderBy"]]
    return []


def parse_order_by_post(order_by_raw: Union[str, List[Union[str, Mapping[str, Any]]], None]) -> List[Union[str, Tuple[str, str]]]:
    if order_by_raw is None:
        return []
    if isinstance(order_by_raw, str):
        return [order_by_raw]
    out: List[Union[str, Tuple[str, str]]] = []
    if isinstance(order_by_raw, list):
        for item in order_by_raw:
            if isinstance(item, str):
                out.append(item)  # ascending by default
            elif isinstance(item, dict) and "field" in item:
                out.append((item["field"], item.get("type", "ascending")))
    return out


def extract_filters(source: Mapping[str, Any], exclude: Iterable[str] = ()) -> Dict[str, Any]:
    exclude_set = set(exclude)
    return {k: v for k, v in source.items() if k not in exclude_set}


async def execute_and_fetch(query_str: str, params: Mapping[str, Any]) -> List[Any]:
    async for db in get_db():
        result = await db.execute(text(query_str), params)
        return result.fetchall()
    return []


def make_info(organism_config, query_info: str) -> Dict[str, Any]:
    return {
        "dataVersion": "0",
        "requestId": str(uuid.uuid4()),
        "requestInfo": f"{organism_config.schema['organismName']} on querulus",
        "queryInfo": query_info,
    }


def dict_rows_to_tsv(rows: List[Dict[str, Any]], explicit_columns: Optional[List[str]] = None) -> str:
    if not rows:
        return ""
    columns = explicit_columns or list(rows[0].keys())
    lines = ["\t".join(columns)]
    for row in rows:
        vals: List[str] = []
        for col in columns:
            value = row.get(col, "")
            if value is None:
                vals.append("")
            elif isinstance(value, (dict, list)):
                vals.append(json.dumps(value))
            else:
                vals.append(str(value))
        lines.append("\t".join(vals))
    return "\n".join(lines)


def maybe_attachment(response: Response, download: bool, basename: Optional[str], data_format: str, default_base: str):
    if download:
        filename = basename or default_base
        if data_format.upper() == "JSON":
            filename += ".json"
        elif data_format.upper() == "TSV":
            filename += ".tsv"
        else:
            filename += ".fasta"
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'


def accession_version(row) -> str:
    return f"{row.accession}.{row.version}"


# =============================================================================
# Shared sequence handling
# =============================================================================


async def handle_nucleotide_sequences(
    *,
    organism: str,
    request: Request,
    segment: str,
    data_format: str,
    limit: Optional[int],
    offset: int,
    filters: Mapping[str, Any],
    builder_query_fn: Callable[[QueryBuilder, str, Optional[int], int], Tuple[str, Dict[str, Any]]],
    default_download_name: str,
    download_as_file: bool = False,
    download_basename: Optional[str] = None,
) -> Response:
    organism_config = validate_organism_or_404(organism)

    # Build query
    builder = QueryBuilder(organism, organism_config)
    builder.add_filters_from_params(dict(filters))
    query_str, params = builder_query_fn(builder, segment, limit, offset)

    rows = await execute_and_fetch(query_str, params)

    # Decompress
    compression = request.app.state.compression
    seqs: List[Dict[str, str]] = []
    for row in rows:
        av = accession_version(row)
        compressed = row.compressed_seq
        if not compressed:
            continue
        try:
            seq = compression.decompress_nucleotide_sequence(compressed, organism, segment)
            seqs.append({"accessionVersion": av, "sequence": seq})
        except Exception as e:
            logger.error(f"Error decompressing {av}: {e}")

    # Format
    if data_format.upper() == "JSON":
        payload = [{"accessionVersion": s["accessionVersion"], segment: s["sequence"]} for s in seqs]
        resp = JSONResponse(content=payload)
    else:
        lines: List[str] = []
        for s in seqs:
            lines.append(f">{s['accessionVersion']}")
            lines.append(s["sequence"])
        resp = Response(content="\n".join(lines), media_type="text/x-fasta")

    # Optional download header
    maybe_attachment(resp, download_as_file, download_basename, data_format, default_download_name)
    return resp


async def handle_amino_acid_sequences(
    *,
    organism: str,
    request: Request,
    gene: str,
    data_format: str,
    limit: Optional[int],
    offset: int,
    filters: Mapping[str, Any],
) -> Response:
    organism_config = validate_organism_or_404(organism)

    builder = QueryBuilder(organism, organism_config)
    builder.add_filters_from_params(dict(filters))
    query_str, params = builder.build_amino_acid_sequences_query(gene, limit, offset)

    rows = await execute_and_fetch(query_str, params)

    compression = request.app.state.compression
    seqs: List[Dict[str, str]] = []
    for row in rows:
        av = accession_version(row)
        compressed = row.compressed_seq
        if not compressed:
            continue
        try:
            seq = compression.decompress_amino_acid_sequence(compressed, organism, gene)
            seqs.append({"accessionVersion": av, "sequence": seq})
        except Exception as e:
            logger.error(f"Error decompressing {av}: {e}")

    if data_format.upper() == "JSON":
        payload = [{"accessionVersion": s["accessionVersion"], gene: s["sequence"]} for s in seqs]
        return JSONResponse(content=payload)
    else:
        lines: List[str] = []
        for s in seqs:
            lines.append(f">{s['accessionVersion']}")
            lines.append(s["sequence"])
        return Response(content="\n".join(lines), media_type="text/x-fasta")


# =============================================================================
# Root / Health / Ready
# =============================================================================


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


# =============================================================================
# Aggregated
# =============================================================================


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
    """
    organism_config = validate_organism_or_404(organism)
    group_by_fields = parse_fields_param(fields)
    order_by_fields = parse_order_by_get(request)

    # Build query
    builder = QueryBuilder(organism, organism_config)
    builder.set_group_by_fields(group_by_fields)
    builder.set_order_by_fields(order_by_fields)
    builder.add_filters_from_params(dict(request.query_params))

    query_str, params = builder.build_aggregated_query(limit, offset)
    rows = await execute_and_fetch(query_str, params)

    # Format results
    if group_by_fields:
        data: List[Dict[str, Any]] = []
        for row in rows:
            rd = {field: row._mapping[field] for field in group_by_fields}
            rd["count"] = row.count
            data.append(rd)
    else:
        data = [{"count": rows[0].count if rows else 0}]

    if dataFormat.upper() == "TSV":
        columns = [*group_by_fields, "count"] if group_by_fields else ["count"]
        tsv = dict_rows_to_tsv(data, explicit_columns=columns)
        return Response(content=tsv, media_type="text/tab-separated-values")

    return {
        "data": data,
        "info": make_info(organism_config, "Aggregated query"),
    }


@app.post("/{organism}/sample/aggregated")
async def post_aggregated(organism: str, body: dict = {}):
    """POST version of aggregated endpoint - accepts JSON body with query parameters."""
    organism_config = validate_organism_or_404(organism)

    group_by_fields = body.get("fields", [])
    limit = body.get("limit")
    offset = body.get("offset", 0)
    order_by_fields = parse_order_by_post(body.get("orderBy", []))

    builder = QueryBuilder(organism, organism_config)
    builder.set_group_by_fields(group_by_fields if isinstance(group_by_fields, list) else [])
    builder.set_order_by_fields(order_by_fields)

    filter_params = extract_filters(
        body,
        exclude=[
            "fields", "limit", "offset", "orderBy",
            "nucleotideMutations", "aminoAcidMutations",
            "nucleotideInsertions", "aminoAcidInsertions",
        ],
    )
    builder.add_filters_from_params(filter_params)

    query_str, params = builder.build_aggregated_query(limit, offset)
    rows = await execute_and_fetch(query_str, params)

    if group_by_fields:
        data: List[Dict[str, Any]] = []
        for row in rows:
            rd = {"count": row.count}
            for field in group_by_fields:
                rd[field] = row._mapping[field]
            data.append(rd)
    else:
        data = [{"count": rows[0].count if rows else 0}]

    return {
        "data": data,
        "info": make_info(organism_config, "Aggregated query"),
    }


# =============================================================================
# Details
# =============================================================================


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
    """
    organism_config = validate_organism_or_404(organism)
    selected_fields = parse_fields_param(fields) or None
    order_by_fields = parse_order_by_get(request)

    builder = QueryBuilder(organism, organism_config)
    builder.set_order_by_fields(order_by_fields)
    query_params = dict(request.query_params)
    builder.add_filters_from_params(query_params)

    query_str, params = builder.build_details_query(selected_fields, limit, offset)

    # Debug parity with original
    if "versionStatus" in query_params:
        print("\n=== DEBUG: Details query with versionStatus filter ===")
        print(f"Query:\n{query_str}")
        print(f"Params: {params}")
        print("=" * 60)

    rows = await execute_and_fetch(query_str, params)
    data = [dict(row._mapping) for row in rows]

    if dataFormat.upper() == "TSV":
        tsv = dict_rows_to_tsv(data)
        return Response(content=tsv, media_type="text/tab-separated-values")

    return {
        "data": data,
        "info": make_info(organism_config, "Details query"),
    }


@app.post("/{organism}/sample/details")
async def post_details(organism: str, body: dict = {}):
    """POST version of details endpoint - accepts JSON body with query parameters."""
    organism_config = validate_organism_or_404(organism)

    fields_list = body.get("fields", [])
    selected_fields = fields_list if isinstance(fields_list, list) and fields_list else None
    limit = body.get("limit")
    offset = body.get("offset", 0)
    order_by_fields = parse_order_by_post(body.get("orderBy", []))

    builder = QueryBuilder(organism, organism_config)
    builder.set_order_by_fields(order_by_fields)

    filter_params = extract_filters(
        body,
        exclude=[
            "fields", "limit", "offset", "orderBy",
            "nucleotideMutations", "aminoAcidMutations",
            "nucleotideInsertions", "aminoAcidInsertions",
        ],
    )
    builder.add_filters_from_params(filter_params)

    query_str, params = builder.build_details_query(selected_fields, limit, offset)
    rows = await execute_and_fetch(query_str, params)
    data = [dict(row._mapping) for row in rows]

    return {
        "data": data,
        "info": make_info(organism_config, "Details query"),
    }


# =============================================================================
# Nucleotide sequences (aligned / unaligned)
# =============================================================================


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
    """
    filters = dict(request.query_params)
    return await handle_nucleotide_sequences(
        organism=organism,
        request=request,
        segment="main",
        data_format=dataFormat,
        limit=limit,
        offset=offset,
        filters=filters,
        builder_query_fn=lambda b, seg, lim, off: b.build_sequences_query(seg, lim, off),
        default_download_name=f"{organism}_sequences",
    )


@app.post("/{organism}/sample/alignedNucleotideSequences")
async def post_aligned_nucleotide_sequences(organism: str, request: Request, body: dict = {}):
    """POST version of aligned nucleotide sequences endpoint - accepts JSON body with query parameters."""
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")
    filters = extract_filters(body, exclude=["limit", "offset", "dataFormat"])

    return await handle_nucleotide_sequences(
        organism=organism,
        request=request,
        segment="main",
        data_format=data_format,
        limit=limit,
        offset=offset,
        filters=filters,
        builder_query_fn=lambda b, seg, lim, off: b.build_sequences_query(seg, lim, off),
        default_download_name=f"{organism}_sequences",
    )


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
    """
    filters = dict(request.query_params)
    resp = await handle_nucleotide_sequences(
        organism=organism,
        request=request,
        segment="main",
        data_format=dataFormat,
        limit=limit,
        offset=offset,
        filters=filters,
        builder_query_fn=lambda b, seg, lim, off: b.build_unaligned_sequences_query(seg, lim, off),
        default_download_name=f"{organism}_sequences",
        download_as_file=downloadAsFile,
        download_basename=downloadFileBasename,
    )
    return resp


@app.post("/{organism}/sample/unalignedNucleotideSequences")
async def post_unaligned_nucleotide_sequences(organism: str, request: Request, body: dict = {}):
    """POST version of unaligned nucleotide sequences endpoint - accepts JSON body with query parameters."""
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")
    filters = extract_filters(body, exclude=["limit", "offset", "dataFormat"])

    return await handle_nucleotide_sequences(
        organism=organism,
        request=request,
        segment="main",
        data_format=data_format,
        limit=limit,
        offset=offset,
        filters=filters,
        builder_query_fn=lambda b, seg, lim, off: b.build_unaligned_sequences_query(seg, lim, off),
        default_download_name=f"{organism}_sequences",
    )


@app.post("/{organism}/sample/unalignedNucleotideSequences/{segment}")
async def post_unaligned_nucleotide_sequences_segment(
    organism: str, segment: str, request: Request, body: dict = {}
):
    """POST version of unaligned nucleotide sequences endpoint with segment parameter."""
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")
    filters = extract_filters(body, exclude=["limit", "offset", "dataFormat"])

    return await handle_nucleotide_sequences(
        organism=organism,
        request=request,
        segment=segment,
        data_format=data_format,
        limit=limit,
        offset=offset,
        filters=filters,
        builder_query_fn=lambda b, seg, lim, off: b.build_unaligned_sequences_query(seg, lim, off),
        default_download_name=f"{organism}_sequences",
    )


# =============================================================================
# Amino Acid sequences (aligned)
# =============================================================================


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
    """
    filters = dict(request.query_params)
    return await handle_amino_acid_sequences(
        organism=organism,
        request=request,
        gene=gene,
        data_format=dataFormat,
        limit=limit,
        offset=offset,
        filters=filters,
    )


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
    """
    limit = body.get("limit")
    offset = body.get("offset", 0)
    data_format = body.get("dataFormat", "FASTA")
    filters = dict(body)

    return await handle_amino_acid_sequences(
        organism=organism,
        request=request,
        gene=gene,
        data_format=data_format,
        limit=limit,
        offset=offset,
        filters=filters,
    )


# =============================================================================
# Insertions (Nucleotide / Amino Acid)
# =============================================================================


@app.post("/{organism}/sample/nucleotideInsertions")
async def post_nucleotide_insertions(organism: str, body: dict = {}):
    """
    Get nucleotide insertions aggregated across all matching sequences.

    Returns list of insertions with counts, positions, and inserted symbols.
    """
    organism_config = validate_organism_or_404(organism)

    # Build query to get all insertions from matching sequences
    builder = QueryBuilder(organism, organism_config)

    # Add filters from body (excluding special fields)
    filter_params: Dict[str, Any] = {}
    for k, v in body.items():
        if k not in [
            "fields", "limit", "offset", "orderBy",
            "nucleotideMutations", "aminoAcidMutations",
            "nucleotideInsertions", "aminoAcidInsertions",
        ]:
            if k == "isRevocation":
                filter_params["is_revocation"] = v if isinstance(v, bool) else str(v).lower() == "true"
            else:
                filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    # Use query builder to build a filtered CTE, then expand insertions
    from querulus.query_builder import FIELD_DEFINITIONS, BASE_TABLE

    # Separate simple and computed filters
    simple_filters: List[Tuple[str, Any]] = []
    computed_filters: List[Tuple[str, Any]] = []
    for field, value in builder.filters.items():
        base_field = field.rstrip("From").rstrip("To")
        if base_field in FIELD_DEFINITIONS and FIELD_DEFINITIONS[base_field].requires_cte:
            computed_filters.append((field, value))
        else:
            simple_filters.append((field, value))

    params: Dict[str, Any] = {"organism": organism}

    if computed_filters:
        # Need CTE for computed field filtering
        filter_base_fields = [field.rstrip("From").rstrip("To") for field, _ in computed_filters]

        select_parts = []
        for field in filter_base_fields:
            if field in FIELD_DEFINITIONS:
                select_parts.append(FIELD_DEFINITIONS[field].select_sql(builder))
        select_parts.append("joint_metadata -> 'nucleotideInsertions' as insertions_all_segments")
        select_clause = ",\n            ".join(select_parts)

        # WHERE for CTE (simple filters only)
        cte_where_clauses = []
        for field, value in simple_filters:
            param_name = f"filter_{field.replace('.', '_')}"
            base_field = field.rstrip("From").rstrip("To")
            field_def = builder._field_definition(base_field)
            filter_expr = field_def.filter_sql(builder)

            if field.endswith("From"):
                op = ">="
            elif field.endswith("To"):
                op = "<="
            else:
                op = "="

            cte_where_clauses.append(f"{filter_expr} {op} :{param_name}")
            params[param_name] = builder._convert_param_value(value, base_field)

        cte_where = f" AND {' AND '.join(cte_where_clauses)}" if cte_where_clauses else ""

        # WHERE for outer query (computed filters)
        outer_where_clauses = []
        for field, value in computed_filters:
            base_field = field.rstrip("From").rstrip("To")
            param_name = f"filter_{field.replace('.', '_')}"
            if field.endswith("From"):
                op = ">="
            elif field.endswith("To"):
                op = "<="
            else:
                op = "="
            outer_where_clauses.append(f'"{base_field}" {op} :{param_name}')
            params[param_name] = value

        outer_where = f"WHERE {' AND '.join(outer_where_clauses)}" if outer_where_clauses else ""

        query_str = f"""
            WITH filtered_sequences AS (
                SELECT
                    {select_clause}
                FROM {BASE_TABLE}
                WHERE organism = :organism
                  AND released_at IS NOT NULL
                  AND joint_metadata -> 'nucleotideInsertions' IS NOT NULL
                  {cte_where}
            ),
            filtered_with_computed AS (
                SELECT insertions_all_segments
                FROM filtered_sequences
                {outer_where}
            ),
            segments_expanded AS (
                SELECT
                    segment_name,
                    insertions_array
                FROM filtered_with_computed,
                LATERAL jsonb_each(insertions_all_segments) AS segments(segment_name, insertions_array)
            ),
            insertions_data AS (
                SELECT
                    segment_name,
                    jsonb_array_elements_text(insertions_array) as insertion_str
                FROM segments_expanded
            ),
            parsed_insertions AS (
                SELECT
                    segment_name,
                    split_part(insertion_str, ':', 1)::int as position,
                    split_part(insertion_str, ':', 2) as inserted_symbols
                FROM insertions_data
            )
            SELECT
                'ins_' || segment_name || ':' || position || ':' || inserted_symbols as insertion,
                COUNT(*) as count,
                inserted_symbols,
                position,
                segment_name as sequence_name
            FROM parsed_insertions
            GROUP BY segment_name, position, inserted_symbols
            ORDER BY count DESC, position ASC
        """
    else:
        # Simple query without CTE for computed fields
        where_clauses = []
        for field, value in simple_filters:
            param_name = f"filter_{field.replace('.', '_')}"
            base_field = field.rstrip("From").rstrip("To")
            field_def = builder._field_definition(base_field)
            filter_expr = field_def.filter_sql(builder)

            if field.endswith("From"):
                op = ">="
            elif field.endswith("To"):
                op = "<="
            else:
                op = "="

            where_clauses.append(f"{filter_expr} {op} :{param_name}")
            params[param_name] = builder._convert_param_value(value, base_field)

        where_clause = f" AND {' AND '.join(where_clauses)}" if where_clauses else ""

        query_str = f"""
            WITH segments_expanded AS (
                SELECT
                    segment_name,
                    insertions_array
                FROM sequence_entries_view,
                LATERAL jsonb_each(joint_metadata -> 'nucleotideInsertions') AS segments(segment_name, insertions_array)
                WHERE organism = :organism
                  AND released_at IS NOT NULL
                  AND joint_metadata -> 'nucleotideInsertions' IS NOT NULL
                  {where_clause}
            ),
            insertions_data AS (
                SELECT
                    segment_name,
                    jsonb_array_elements_text(insertions_array) as insertion_str
                FROM segments_expanded
            ),
            parsed_insertions AS (
                SELECT
                    segment_name,
                    split_part(insertion_str, ':', 1)::int as position,
                    split_part(insertion_str, ':', 2) as inserted_symbols
                FROM insertions_data
            )
            SELECT
                'ins_' || segment_name || ':' || position || ':' || inserted_symbols as insertion,
                COUNT(*) as count,
                inserted_symbols,
                position,
                segment_name as sequence_name
            FROM parsed_insertions
            GROUP BY segment_name, position, inserted_symbols
            ORDER BY count DESC, position ASC
        """

    rows = await execute_and_fetch(query_str, params)
    data = [
        {
            "insertion": row.insertion,
            "count": row.count,
            "insertedSymbols": row.inserted_symbols,
            "position": row.position,
            "sequenceName": row.sequence_name,
        }
        for row in rows
    ]

    return {
        "data": data,
        "info": make_info(organism_config, "Nucleotide insertions query"),
    }


@app.post("/{organism}/sample/aminoAcidInsertions")
async def post_amino_acid_insertions(organism: str, body: dict = {}):
    """
    Get amino acid insertions aggregated across all matching sequences.

    Returns list of insertions with counts, positions, gene names, and inserted symbols.
    """
    organism_config = validate_organism_or_404(organism)

    builder = QueryBuilder(organism, organism_config)

    # Add filters from body (excluding special fields)
    filter_params: Dict[str, Any] = {}
    for k, v in body.items():
        if k not in [
            "fields", "limit", "offset", "orderBy",
            "nucleotideMutations", "aminoAcidMutations",
            "nucleotideInsertions", "aminoAcidInsertions",
        ]:
            if k == "isRevocation":
                filter_params["is_revocation"] = v if isinstance(v, bool) else str(v).lower() == "true"
            else:
                filter_params[k] = v
    builder.add_filters_from_params(filter_params)

    params = {"organism": organism}

    # Build WHERE clause for filters (preserve original behavior)
    where_clauses = []
    for field, value in builder.filters.items():
        param_name = f"filter_{field}"
        where_clauses.append(f"joint_metadata -> 'metadata' ->> '{field}' = :{param_name}")
        params[param_name] = value

    where_clause = f" AND {' AND '.join(where_clauses)}" if where_clauses else ""

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

    rows = await execute_and_fetch(query_str, params)
    data = [
        {
            "insertion": row.insertion,
            "count": row.count,
            "insertedSymbols": row.inserted_symbols,
            "position": row.position,
            "sequenceName": row.sequence_name,
        }
        for row in rows
    ]

    return {
        "data": data,
        "info": make_info(organism_config, "Amino acid insertions query"),
    }


# =============================================================================
# Mutations (Nucleotide / Amino Acid)
# =============================================================================


async def _aligned_sequences_metadata(organism: str, params: Mapping[str, Any], request: Request):
    organism_config = validate_organism_or_404(organism)
    builder = QueryBuilder(organism, organism_config)
    builder.add_filters_from_params(dict(params))
    query_str, qparams = builder.build_aligned_sequences_metadata_query(limit=None, offset=0)
    rows = await execute_and_fetch(query_str, qparams)
    return organism_config, rows


@app.get("/{organism}/sample/nucleotideMutations")
async def get_nucleotide_mutations(organism: str, request: Request):
    """Get nucleotide mutations for matching sequences"""
    organism_config, rows = await _aligned_sequences_metadata(organism, request.query_params, request)

    all_mutations: List[Dict[str, Any]] = []
    compression = request.app.state.compression

    for row in rows:
        aligned_sequences = row.aligned_sequences or {}
        for segment_name, seq_data in aligned_sequences.items():
            if not seq_data or "compressedSequence" not in seq_data:
                continue
            try:
                sequence = compression.decompress_nucleotide_sequence(
                    seq_data["compressedSequence"], organism, segment_name
                )
                reference_seq = organism_config.referenceGenome.get_nucleotide_sequence(segment_name)
                if not reference_seq:
                    continue
                for i, (ref_base, seq_base) in enumerate(zip(reference_seq, sequence)):
                    if ref_base != seq_base and seq_base != "N":
                        position = i + 1
                        all_mutations.append({
                            "mutation": f"{ref_base}{position}{seq_base}",
                            "mutationFrom": ref_base,
                            "mutationTo": seq_base,
                            "position": position,
                            "sequenceName": None,
                            "count": 1,
                            "coverage": 1,
                            "proportion": 1.0,
                        })
            except Exception as e:
                logger.error(f"Error calculating mutations for {row.accession}.{row.version}: {e}")

    return {
        "data": all_mutations,
        "info": make_info(organism_config, "Nucleotide mutations query"),
    }


@app.post("/{organism}/sample/nucleotideMutations")
async def post_nucleotide_mutations(
    organism: str,
    request: Request,
    body: dict = Body({}),
):
    """POST version of nucleotide mutations endpoint (preserves GET behavior)."""
    merged = dict(request.query_params)
    merged.update(body or {})
    return await get_nucleotide_mutations(organism, type("FakeReq", (), {"query_params": merged, "app": request.app})())


@app.get("/{organism}/sample/aminoAcidMutations")
async def get_amino_acid_mutations(organism: str, request: Request):
    """Get amino acid mutations for matching sequences"""
    organism_config, rows = await _aligned_sequences_metadata(organism, request.query_params, request)

    all_mutations: List[Dict[str, Any]] = []
    compression = request.app.state.compression

    for row in rows:
        aa_sequences = row.amino_acid_sequences or {}
        for gene_name, seq_data in aa_sequences.items():
            if not seq_data or "compressedSequence" not in seq_data:
                continue
            try:
                sequence = compression.decompress_amino_acid_sequence(
                    seq_data["compressedSequence"], organism, gene_name
                )
                reference_seq = organism_config.referenceGenome.get_gene_sequence(gene_name)
                if not reference_seq:
                    continue

                for i, (ref_aa, seq_aa) in enumerate(zip(reference_seq, sequence)):
                    if ref_aa != seq_aa and seq_aa != "X":
                        all_mutations.append({
                            "mutation": f"{gene_name}:{ref_aa}{i+1}{seq_aa}",
                            "mutationFrom": ref_aa,
                            "mutationTo": seq_aa,
                            "position": i + 1,
                            "count": 1,
                            "coverage": 1,
                            "proportion": 1.0,
                            "sequenceName": gene_name,
                        })
            except Exception as e:
                logger.error(f"Error processing {gene_name} for {row.accession}.{row.version}: {e}")

    return {
        "data": all_mutations,
        "info": make_info(organism_config, "Amino acid mutations query"),
    }


@app.post("/{organism}/sample/aminoAcidMutations")
async def post_amino_acid_mutations(
    organism: str,
    request: Request,
    body: dict = Body({}),
):
    """POST version of amino acid mutations endpoint (preserves GET behavior)."""
    merged = dict(request.query_params)
    merged.update(body or {})
    return await get_amino_acid_mutations(organism, type("FakeReq", (), {"query_params": merged, "app": request.app})())


# =============================================================================
# Entrypoint
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
