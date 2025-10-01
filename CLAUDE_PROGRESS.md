# Claude Progress Tracker

## Purpose

This file tracks implementation progress for the Querulus project. It should be updated at the start and end of each work session to maintain continuity across conversations.

## How to Use This File

### At Start of Session
1. Read this file to understand current state
2. Review "Next Steps" section
3. Update "Current Status" if continuing work from last session
4. Add any blockers or questions discovered

### During Session
- Update task statuses as work progresses
- Add notes about decisions made
- Document any deviations from the plan
- Record useful commands, queries, or code snippets

### At End of Session
- Mark completed tasks
- Update "Next Steps" with 3-5 specific actionable items
- Note any pending questions or blockers
- Commit progress to git (if applicable)

---

## Current Status

**Date**: 2025-10-01
**Phase**: Planning Complete
**Working On**: Planning and architecture design

### Completed Tasks

- ✅ Analyzed LAPIS API specification
- ✅ Examined Loculus database schema
- ✅ Studied LAPIS source code and architecture
- ✅ Connected to PostgreSQL database and explored schema
- ✅ Analyzed sequence compression implementation (Zstandard with dictionary compression)
- ✅ Created comprehensive PLAN.md with full architecture and implementation strategy

### Key Findings

1. **Database Schema**:
   - Primary table: `sequence_entries_view` (joins entries + preprocessed data + external metadata)
   - All metadata stored in JSONB: `joint_metadata -> 'metadata' ->> 'fieldName'`
   - Sequences stored as Base64-encoded Zstandard-compressed data
   - 8,324 west-nile sequences in test database

2. **Compression Algorithm**:
   - Uses Zstandard (zstd) level 3 compression
   - **Dictionary compression** using reference genome sequences
   - Defined in `CompressionService.kt` in Loculus backend
   - Provides 50-70% better compression than standard zstd

3. **API Endpoints to Implement**:
   - `/sample/aggregated` - Count/group sequences by metadata
   - `/sample/details` - Get metadata for sequences
   - `/sample/alignedNucleotideSequences/{segment}` - Get nucleotide sequences
   - `/sample/alignedAminoAcidSequences/{gene}` - Get amino acid sequences
   - `/sample/nucleotideInsertions` - List nucleotide insertions
   - `/sample/aminoAcidInsertions` - List amino acid insertions
   - **Skipping**: Mutation-based filtering (Phase 1)

4. **Technology Stack Decision**:
   - **Python + FastAPI** for rapid development
   - asyncpg/SQLAlchemy for PostgreSQL
   - zstandard library for decompression
   - Can optimize hot paths later if needed

---

## Next Steps

### Immediate (Next Session)

1. **Set up Python project structure**:
   - Create `querulus/` directory
   - Set up pyproject.toml with dependencies (fastapi, uvicorn, asyncpg, sqlalchemy, zstandard)
   - Create basic FastAPI app skeleton
   - Set up development environment

2. **Create Kubernetes ConfigMap for Querulus**:
   - Create `kubernetes/loculus/templates/querulus-config.yaml`
   - Reuse `generateBackendConfig` helper to generate config with reference genomes
   - Test with `helm template` to verify output

3. **Implement configuration loading**:
   - Create `config.py` module to load querulus_config.json
   - Parse organism configs and reference genomes
   - Create SequenceDecompressor class

4. **Database connection**:
   - Set up async PostgreSQL connection pool
   - Create database session dependency for FastAPI
   - Test basic query: `SELECT COUNT(*) FROM sequence_entries_view WHERE organism='west-nile'`

5. **First endpoint - Simple aggregation**:
   - Implement `GET /west-nile/sample/aggregated` (no filters)
   - Return total count in LAPIS response format
   - Add basic error handling

### Phase 1: MVP (Weeks 1-2)

- [ ] QueryBuilder class for translating LAPIS params to SQL
- [ ] `/sample/aggregated` with field grouping (country, lineage, etc.)
- [ ] Metadata filtering (WHERE clauses on JSONB fields)
- [ ] `/sample/details` endpoint with field selection
- [ ] Integration tests comparing against live LAPIS
- [ ] Response formatting (JSON)

### Phase 2: Sequences (Weeks 3-4)

- [ ] Sequence decompression with dictionary
- [ ] `/sample/alignedNucleotideSequences/main` (FASTA)
- [ ] `/sample/alignedAminoAcidSequences/{gene}` (FASTA)
- [ ] FASTA header template parsing
- [ ] Streaming responses for large datasets
- [ ] Memory optimization

### Phase 3+: See PLAN.md

---

## Active Blockers

**None currently**

---

## Open Questions

1. **Data versioning**: How should we implement `dataVersion` header?
   - Option A: Use `table_update_tracker` table timestamps
   - Option B: Use max(updated_at) from sequence_entries_view
   - Option C: Simple incrementing counter in Redis/DB

2. **Authentication**: Does LAPIS implement authentication?
   - Need to check for access key logic in LAPIS codebase
   - May need to implement data access controls

3. **Performance**: What indexes are needed on JSONB fields?
   - Start with GIN indexes on commonly queried fields
   - Monitor query performance with EXPLAIN ANALYZE
   - May need generated columns for hot paths

4. **Multi-organism support**: Route by path prefix or query param?
   - Current LAPIS uses path: `/{organism}/sample/...`
   - Need to extract organism from path in FastAPI

---

## Useful Commands

### Database Queries

```bash
# Connect to database
/opt/homebrew/opt/postgresql@15/bin/psql -h localhost -U postgres -d loculus

# Count released sequences by organism
SELECT organism, COUNT(*)
FROM sequence_entries_view
WHERE released_at IS NOT NULL
GROUP BY organism;

# Get sample metadata
SELECT
  accession,
  version,
  joint_metadata -> 'metadata' ->> 'geoLocCountry' AS country,
  joint_metadata -> 'metadata' ->> 'lineage' AS lineage
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
LIMIT 5;
```

### Kubernetes/Helm

```bash
# Generate Loculus backend config
cd kubernetes
helm template loculus . --set environment=main | grep -A 50 "kind: ConfigMap"

# Port-forward to PostgreSQL
kubectl port-forward -n prev-main $(kubectl get pods -n prev-main -l app=postgres -o name) 5432:5432
```

### Testing Against LAPIS

```bash
# Test aggregated endpoint
curl https://lapis-main.loculus.org/west-nile/sample/aggregated

# Test details endpoint
curl https://lapis-main.loculus.org/west-nile/sample/details?limit=5

# Test with filters
curl "https://lapis-main.loculus.org/west-nile/sample/aggregated?country=USA"
```

---

## Design Decisions Log

### 2025-10-01: Technology Stack
**Decision**: Use Python + FastAPI for initial implementation
**Rationale**:
- Fastest development velocity for prototyping
- Excellent PostgreSQL async support
- Easy JSON/data manipulation
- Can optimize hot paths in Rust/Go later if needed
- Team likely familiar with Python

### 2025-10-01: Skip Mutation Filtering (Phase 1)
**Decision**: Do not implement mutation/insertion/deletion-based filtering in initial release
**Rationale**:
- Per user request - reduces scope
- Most complex feature of LAPIS
- Can be added later without breaking API compatibility
- Focus on core metadata querying first

### 2025-10-01: Reuse Backend Config Generation
**Decision**: Create querulus-config.yaml that reuses `generateBackendConfig` helper
**Rationale**:
- Ensures exact same reference genomes as backend
- No config drift between services
- Leverages existing, tested config generation
- Single source of truth for organism configs

---

## Performance Metrics to Track

Target SLOs (from PLAN.md):
- **Availability**: 99.9% uptime
- **Latency**:
  - P50 < 50ms for aggregated queries
  - P95 < 200ms for aggregated queries
  - P50 < 300ms for sequence queries (100 sequences)
  - P95 < 1s for sequence queries (100 sequences)
- **Throughput**: 100+ req/s per instance
- **Resources**: < 1GB memory per instance

### Baseline Measurements
- TODO: Benchmark LAPIS response times
- TODO: Measure current database query performance
- TODO: Profile memory usage of decompression

---

## Notes for Next Claude

- PLAN.md contains full architecture and implementation strategy
- Database schema is well-documented in PLAN.md Appendix A
- Compression algorithm is Zstandard with dictionary compression (reference genome as dict)
- Focus on MVP: aggregated + details endpoints first, sequences later
- Test database is at localhost:5432, database=loculus, user=postgres, password=unsecure
- Live LAPIS for testing: https://lapis-main.loculus.org/west-nile/
