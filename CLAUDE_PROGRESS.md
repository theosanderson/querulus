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

**Date**: 2025-10-02
**Phase**: Feature Parity with LAPIS
**Working On**: Expanding Querulus to match more LAPIS features (POST endpoints, insertions, etc.)

### Completed Tasks (Summary)

#### ✅ Core Implementation Complete
- **Planning & Architecture**: Full analysis of LAPIS, database schema, compression algorithm
- **Project Setup**: Python + FastAPI + async PostgreSQL with pyproject.toml
- **Configuration**: Kubernetes ConfigMap, config loader with reference genome processing
- **Health Checks**: `/health` and `/ready` endpoints with database connection verification

#### ✅ API Endpoints (All Working)
- **GET Aggregated** (`/{organism}/sample/aggregated`): Grouping, filtering, pagination, orderBy
- **GET Details** (`/{organism}/sample/details`): Field selection, filtering, pagination, orderBy
- **GET Sequences** (aligned/unaligned nucleotide, amino acid): FASTA format, decompression, filtering
- All endpoints support metadata filtering, computed field filtering, and sorting

#### ✅ Computed Fields (Complete)
All fields from LAPIS `ReleasedDataModel.kt` implemented:
- Version fields: accessionVersion, displayName, versionStatus (LATEST_VERSION/REVISED/REVOKED)
- Timestamps: submittedDate, releasedDate, submittedAtTimestamp, releasedAtTimestamp, earliestReleaseDate
- Metadata: submissionId, submitter, groupId, groupName (with JOIN), isRevocation, versionComment
- Data use: dataUseTerms (OPEN/RESTRICTED), dataUseTermsRestrictedUntil, dataUseTermsUrl

#### ✅ Advanced Features
- **Window Functions**: versionStatus and earliestReleaseDate use SQL window functions
- **CTE Support**: Computed fields in CTEs for filtering in outer queries
- **Sequence Decompression**: Zstandard with dictionary compression (reference genome)
- **orderBy**: Supports metadata fields, computed fields, random, and count
- **Multi-version handling**: Correctly tracks version history across accessions

#### ✅ Testing & Verification
- **23/23 integration tests passing** (100% success rate)
- All responses match LAPIS exactly (counts, fields, computed values)
- Test suite: `python -m pytest tests/test_lapis_compatibility.py -v`

#### ✅ Docker & Kubernetes
- Dockerfile with multi-stage build (Python 3.12 slim)
- GitHub Actions CI/CD (builds and pushes to ghcr.io)
- Kubernetes manifests in loculus submodule (deployment, service, ingress)
- `useQuerulus` toggle to switch from LAPIS to Querulus

#### ✅ Recent Bug Fixes & Features (2025-10-02)
- **versionStatus filter bug**: Removed from `special_params` - now filters work correctly
- **Computed field filtering**: CTE-based approach for filtering by versionStatus, earliestReleaseDate
- **Insertion endpoints**: Implemented nucleotide and amino acid insertions with LAPIS-compatible aggregation
- **Data format support**: Added JSON format for sequences, TSV format for aggregated/details
- **Range query support**: Full implementation of `{field}From` and `{field}To` parameters with automatic type casting
- All tests now passing (36/36)

### Current Working State

**Server Status**: Running on `localhost:8000`, connects to PostgreSQL at `localhost:5432/loculus`
**Test Database**: 8,324 west-nile sequences
**Test Suite**: 36/36 integration tests passing (100% success rate)

### Key Technical Details

- **Database**: `sequence_entries_view` table with JSONB metadata at `joint_metadata -> 'metadata'`
- **Compression**: Zstandard level 3 with dictionary (reference genome), base64-encoded
- **Stack**: Python 3.12 + FastAPI + asyncpg + zstandard
- **Deployment**: Docker + Kubernetes with `useQuerulus` toggle

---

## Next Steps

### Immediate (Next Session) - Feature Parity

1. ✅ **POST support for aggregated and details endpoints** (ALREADY IMPLEMENTED):
   - `POST /{organism}/sample/aggregated` ✓
   - `POST /{organism}/sample/details` ✓
   - Accepts JSON body with query parameters
   - Supports versionStatus, isRevocation, fields, limit, offset, orderBy

2. ✅ **Insertion endpoints** (COMPLETED 2025-10-02):
   - `POST /{organism}/sample/nucleotideInsertions` ✓
   - `POST /{organism}/sample/aminoAcidInsertions` ✓
   - Parses insertions from JSONB metadata
   - Returns aggregated counts by position and symbols
   - Matches LAPIS response format exactly
   - Tested: counts match LAPIS perfectly

3. ✅ **Add data format support** (COMPLETED 2025-10-02):
   - `dataFormat` parameter for all sequence endpoints ✓
   - JSON format: Returns array of `{accessionVersion, segmentName}` objects ✓
   - TSV format: For aggregated/details endpoints with tab-separated values ✓
   - FASTA remains default for sequences, JSON default for aggregated/details ✓
   - All formats tested and matching LAPIS exactly ✓
   - 6 new tests added to test suite (29/29 tests passing) ✓

4. ✅ **Range query support** (COMPLETED 2025-10-02):
   - LAPIS-style range filtering with `{field}From` and `{field}To` parameters ✓
   - Works for both date and numeric fields ✓
   - Automatic type detection from organism config schema ✓
   - Proper SQL casting for int, float, and date types ✓
   - Parameter value conversion (strings → int/float/date objects) for asyncpg ✓
   - Tested with dates: `ncbiReleaseDateFrom=2010-01-01&ncbiReleaseDateTo=2015-12-31` (2,268 matches) ✓
   - Tested with integers: `lengthFrom=10000&lengthTo=11000` (4,589 matches) ✓
   - Works with aggregated, details, and sequence endpoints ✓
   - Works with grouping and filtering combinations ✓
   - 7 new tests added to test suite (36/36 tests passing) ✓

5. **Additional computed field improvements**:
   - Review LAPIS for any missing computed fields
   - Ensure all field names match exactly (case-sensitive)
   - Test edge cases (null values, missing data, etc.)

6. **Error handling improvements**:
   - Better error messages for invalid parameters
   - HTTP 400 for bad requests with clear error descriptions
   - HTTP 404 for invalid organisms
   - Match LAPIS error response format

### Later Priorities

1. **Optimize sequence streaming** (if needed):
   - Profile memory usage during sequence decompression
   - Consider streaming responses for very large result sets
   - Batch decompression to avoid blocking

3. **Performance testing**:
   - Benchmark response times against LAPIS
   - Test under load (100+ req/s)
   - Monitor memory usage
   - Add query caching if needed


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

**IMPORTANT**: Config must be processed with config-processor to fetch reference genomes!

```bash
# Step 1: Generate querulus_config.json from helm template
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
" > ../../config_unprocessed/querulus_config.json

# Step 2: Process config to fetch reference genomes from URLs
cd ../..
python loculus/kubernetes/config-processor/config-processor.py config_unprocessed config

# This replaces [[URL:...]] placeholders with actual sequence data
# Without this step, decompression will fail!
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
```bash
# Start server
python -m uvicorn querulus.main:app --host 0.0.0.0 --port 8000

# Run tests
python -m pytest tests/test_lapis_compatibility.py -v

# Test endpoint
curl http://localhost:8000/west-nile/sample/aggregated
```

### Key Files
- `querulus/main.py` - FastAPI app with all endpoints
- `querulus/query_builder.py` - SQL query builder (handles filtering, CTE generation)
- `querulus/compression.py` - Zstandard decompression with dictionary
- `querulus/config.py` - Config loader (backend config + reference genomes)
- `tests/test_lapis_compatibility.py` - 23 integration tests (all passing)

### Important Context
- **All core GET endpoints working**: aggregated, details, sequences (nucleotide & amino acid)
- **All computed fields implemented**: versionStatus, earliestReleaseDate, timestamps, etc.
- **Test database**: localhost:5432/loculus (user: postgres, password: unsecure), 8,324 sequences
- **LAPIS reference**: https://lapis-main.loculus.org/west-nile/
- See PLAN.md for full architecture details

### What Needs Work
Focus on feature parity:
1. POST endpoints for aggregated/details
2. Insertion endpoints (nucleotideInsertions, aminoAcidInsertions)
3. JSON format support for sequences (currently only FASTA)
4. Error handling improvements
