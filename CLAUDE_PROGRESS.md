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
**Phase**: MVP Development - Phase 1
**Working On**: Core aggregated and details endpoints

### Completed Tasks

#### Planning & Architecture ✅
- ✅ Analyzed LAPIS API specification
- ✅ Examined Loculus database schema
- ✅ Studied LAPIS source code and architecture
- ✅ Connected to PostgreSQL database and explored schema
- ✅ Analyzed sequence compression implementation (Zstandard with dictionary compression)
- ✅ Created comprehensive PLAN.md with full architecture and implementation strategy

#### Initial Implementation ✅
- ✅ Set up Python project structure with pyproject.toml
- ✅ Created configuration loading module (config.py)
- ✅ Created Kubernetes ConfigMap template (querulus-config.yaml)
- ✅ Generated querulus_config.json from helm template
- ✅ Implemented async PostgreSQL connection pool (database.py)
- ✅ Created FastAPI application with lifespan management (main.py)
- ✅ Implemented health check endpoints (/health, /ready)
- ✅ **Implemented first working endpoint: `GET /{organism}/sample/aggregated`**
- ✅ **VERIFIED: Returns 8324 sequences for west-nile (exact match with LAPIS!)**

### Current Working State

**Querulus is running successfully!** 🎉

- Server running on `localhost:8000`
- Successfully connects to PostgreSQL database
- Basic aggregation endpoint working
- Returns accurate counts matching LAPIS exactly

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

1. **Expand aggregated endpoint**:
   - Add support for `fields` parameter (group by country, lineage, etc.)
   - Implement metadata filtering (WHERE clauses on JSONB fields)
   - Add `orderBy`, `limit`, `offset` parameters
   - Test with: `GET /west-nile/sample/aggregated?fields=geoLocCountry`

2. **Implement details endpoint**:
   - Create `GET /{organism}/sample/details`
   - Support `fields` parameter for field selection
   - Implement filtering same as aggregated
   - Return metadata in LAPIS-compatible format
   - Test against LAPIS to verify field names match

3. **Add response formatting**:
   - Support CSV and TSV output formats
   - Content negotiation based on Accept header or format parameter
   - Streaming responses for large result sets

4. **Query builder abstraction**:
   - Create `QueryBuilder` class to translate LAPIS params to SQL
   - Handle JSONB operators for metadata fields
   - Support numerical comparisons (>, <, >=, <=)
   - Date range filtering

5. **Testing & validation**:
   - Write integration tests comparing Querulus vs LAPIS
   - Test edge cases (empty results, invalid organisms, etc.)
   - Performance testing with EXPLAIN ANALYZE

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

### Running Querulus

```bash
# Start server (foreground)
cd /Users/theosanderson/querulus
python -m querulus.main

# Start server (background)
python -m querulus.main &

# Test endpoints
curl http://localhost:8000/
curl http://localhost:8000/health
curl http://localhost:8000/west-nile/sample/aggregated
```

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

### Regenerating Config

```bash
# Generate querulus_config.json from helm template
cd loculus/kubernetes
helm template test-release loculus/ --set environment=server 2>&1 | python3 -c "
import sys, json
content = sys.stdin.read()
start_idx = content.find('querulus_config.json: |')
json_start = content.find('\n', start_idx) + 1
json_end = content.find('\n---', json_start)
lines = content[json_start:json_end].split('\n')
cleaned = [line[4:] if line.startswith('    ') else line for line in lines]
data = json.loads('\n'.join(cleaned).strip())
print(json.dumps(data, indent=2))
" > ../../config/querulus_config.json
```

### Testing Against LAPIS

```bash
# Compare counts
echo "LAPIS:" && curl -s https://lapis-main.loculus.org/west-nile/sample/aggregated | jq '.data[0].count'
echo "Querulus:" && curl -s http://localhost:8000/west-nile/sample/aggregated | jq '.data[0].count'

# Test details endpoint
curl https://lapis-main.loculus.org/west-nile/sample/details?limit=5 | jq

# Test with filters
curl "https://lapis-main.loculus.org/west-nile/sample/aggregated?fields=geoLocCountry"
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

### Quick Start
- **Server is already implemented and working!** Run: `python -m querulus.main`
- Basic aggregated endpoint working: `curl http://localhost:8000/west-nile/sample/aggregated`
- Returns 8324 sequences (verified exact match with LAPIS)

### Key Files
- `querulus/main.py` - FastAPI app with basic aggregated endpoint
- `querulus/config.py` - Configuration loading (backend config + reference genomes)
- `querulus/database.py` - Async PostgreSQL connection pool
- `config/querulus_config.json` - Generated from helm template (gitignored)

### Architecture
- PLAN.md contains full architecture and implementation strategy
- Database schema is well-documented in PLAN.md Appendix A
- Compression algorithm is Zstandard with dictionary compression (reference genome as dict)
- Focus on MVP: aggregated + details endpoints first, sequences later

### Environment
- Test database: localhost:5432, database=loculus, user=postgres, password=unsecure
- Live LAPIS for testing: https://lapis-main.loculus.org/west-nile/
- Config generated from: `loculus/kubernetes/loculus/templates/querulus-config.yaml`

### What's Working
- ✅ Server starts successfully and loads config
- ✅ Database connection pool initialized
- ✅ Health checks working (/health, /ready)
- ✅ Basic aggregated endpoint (total count only)
- ✅ Accurate results matching LAPIS

### What Needs Work
- ❌ Aggregated with field grouping (e.g., `?fields=geoLocCountry`)
- ❌ Metadata filtering
- ❌ Details endpoint
- ❌ Sequence endpoints
- ❌ Multiple output formats (CSV, TSV, FASTA)
