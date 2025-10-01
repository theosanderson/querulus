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
**Phase**: MVP Development - Phase 1 COMPLETE + Testing Infrastructure
**Working On**: All metadata endpoints working + Comprehensive test suite added

### Completed Tasks

#### Planning & Architecture ✅
- ✅ Analyzed LAPIS API specification
- ✅ Examined Loculus database schema
- ✅ Studied LAPIS source code and architecture
- ✅ Connected to PostgreSQL database and explored schema
- ✅ Analyzed sequence compression implementation (Zstandard with dictionary compression)
- ✅ Created comprehensive PLAN.md with full architecture and implementation strategy
- ✅ Analyzed get-released-data endpoint to understand computed fields

#### Initial Implementation ✅
- ✅ Set up Python project structure with pyproject.toml
- ✅ Created configuration loading module (config.py)
- ✅ Created Kubernetes ConfigMap template (querulus-config.yaml)
- ✅ Generated querulus_config.json from helm template
- ✅ Implemented async PostgreSQL connection pool (database.py)
- ✅ Created FastAPI application with lifespan management (main.py)
- ✅ Implemented health check endpoints (/health, /ready)

#### Core Endpoints ✅
- ✅ **Aggregated endpoint with full functionality:**
  - Field grouping (e.g., `?fields=geoLocCountry`)
  - Metadata filtering (e.g., `?geoLocCountry=USA`)
  - Pagination (limit, offset)
  - versionStatus support (LATEST_VERSION, REVISED, REVOKED)
  - CTE-based approach for window functions in GROUP BY

- ✅ **Details endpoint with full functionality:**
  - Field selection (e.g., `?fields=accession,geoLocCountry,lineage`)
  - Metadata filtering
  - Pagination (limit, offset)
  - Computed fields (accessionVersion, displayName, timestamps, versionStatus)

- ✅ **Computed fields implementation (ALL fields from ReleasedDataModel.kt):**
  - accessionVersion: `accession.version` format
  - displayName: same as accessionVersion
  - submittedDate/releasedDate: formatted dates (YYYY-MM-DD)
  - submittedAtTimestamp/releasedAtTimestamp: Unix timestamps
  - versionStatus: dynamically computed using window functions
  - earliestReleaseDate: minimum of release_at, external date fields (ncbiReleaseDate), and previous versions
  - submissionId, submitter, groupId: directly from database columns
  - isRevocation, versionComment: directly from database columns
  - groupName: JOIN to groups_table
  - dataUseTerms: computed from data_use_terms_table (OPEN/RESTRICTED based on restriction date)
  - dataUseTermsRestrictedUntil: restriction date if currently restricted
  - dataUseTermsUrl: config-driven URLs based on OPEN/RESTRICTED status

- ✅ **versionStatus computation:**
  - LATEST_VERSION: highest version for an accession
  - REVISED: not latest, no revocation exists
  - REVOKED: not latest, higher version is a revocation
  - Uses MAX(version) OVER (PARTITION BY accession)
  - Works in both details and aggregated endpoints

- ✅ **earliestReleaseDate computation:**
  - Computes earliest of: released_at, external fields (from config), previous versions
  - Uses window function: MIN(...) OVER (PARTITION BY accession ORDER BY version)
  - Config-driven: reads externalFields from organism config (e.g., ncbiReleaseDate)
  - Works in both details and aggregated endpoints via CTE
  - Correctly inherits earliest date across all versions of same accession

#### Testing & Verification ✅
- ✅ All endpoints tested against live LAPIS
- ✅ Exact match on counts, grouping, and computed fields
- ✅ Tested with multi-version sequences (LOC_000LUQJ v1 + v2)
- ✅ versionStatus correctly shows REVISED for old versions
- ✅ earliestReleaseDate tested: exact match with LAPIS (444 sequences for 2014-06-30)
- ✅ All computed fields tested and matching LAPIS:
  - submissionId, submitter, groupId, isRevocation, versionComment
  - groupName (with JOIN to groups_table)
  - dataUseTerms, dataUseTermsRestrictedUntil, dataUseTermsUrl
- ✅ **Comprehensive test suite** (tests/test_lapis_compatibility.py):
  - 17 integration tests comparing Querulus vs LAPIS
  - All tests passing (100% success rate)
  - Tests cover: aggregated, details, computed fields, filtering, pagination
  - Can be run with: `python -m pytest tests/test_lapis_compatibility.py -v`

#### Bug Fixes ✅
- ✅ **Computed field filtering support** (2025-10-02):
  - Fixed filtering by versionStatus, earliestReleaseDate, and other computed fields
  - Uses CTE approach: compute fields in inner query, filter in outer query
  - Regular metadata fields filter in CTE WHERE, computed fields filter in outer WHERE
  - Works for both aggregated and details endpoints

### Current Working State

**Querulus is fully functional for metadata queries!** 🎉

- Server running on `localhost:8000`
- Successfully connects to PostgreSQL database
- Both aggregated and details endpoints fully working
- Returns accurate counts and metadata matching LAPIS exactly
- Handles multi-version sequences correctly
- All computed fields working (accessionVersion, timestamps, versionStatus, earliestReleaseDate)
- Filtering by computed fields now fully supported
- 17/17 integration tests passing

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

**Phase 2: Sequence Endpoints**

1. **Implement sequence decompression**:
   - Create decompression module using zstandard library
   - Load reference genome sequences from config as dictionaries
   - Test decompression with sample sequences from database
   - Handle Base64 decoding of compressed data

2. **Implement nucleotide sequence endpoint**:
   - Create `GET /{organism}/sample/alignedNucleotideSequences/{segment}`
   - Return sequences in FASTA format
   - Support filtering, limit, offset
   - Implement FASTA header templating
   - Stream responses for memory efficiency

3. **Implement amino acid sequence endpoint**:
   - Create `GET /{organism}/sample/alignedAminoAcidSequences/{gene}`
   - Similar to nucleotide endpoint but for genes
   - FASTA format with appropriate headers

4. **Add response formatting**:
   - Support CSV and TSV output formats for aggregated/details
   - Content negotiation based on Accept header or format parameter
   - Streaming responses for large result sets

5. **Additional metadata fields**:
   - Add support for more computed fields from get-released-data
   - groupId, groupName, submitter, submissionId
   - dataUseTerms fields (if enabled)
   - isRevocation, versionComment

### Phase 1: MVP ✅ COMPLETE

- ✅ QueryBuilder class for translating LAPIS params to SQL
- ✅ `/sample/aggregated` with field grouping (country, lineage, etc.)
- ✅ Metadata filtering (WHERE clauses on JSONB fields)
- ✅ `/sample/details` endpoint with field selection
- ✅ Integration tests comparing against live LAPIS
- ✅ Response formatting (JSON)
- ✅ Computed fields (accessionVersion, timestamps, versionStatus)
- ✅ Multi-version sequence handling

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

### What's Working ✅
- ✅ Server starts successfully and loads config
- ✅ Database connection pool initialized
- ✅ Health checks working (/health, /ready)
- ✅ Aggregated endpoint with full functionality:
  - Total counts
  - Field grouping (e.g., `?fields=geoLocCountry`, `?fields=earliestReleaseDate`)
  - Metadata filtering (e.g., `?geoLocCountry=USA`)
  - Pagination (limit, offset)
  - versionStatus grouping
  - earliestReleaseDate grouping
- ✅ Details endpoint with full functionality:
  - Field selection (e.g., `?fields=accession,lineage,versionStatus,earliestReleaseDate`)
  - Metadata filtering
  - Pagination
  - All computed fields from ReleasedDataModel.kt
- ✅ Computed fields (ALL FIELDS):
  - accessionVersion (e.g., LOC_00004X1.1)
  - displayName
  - submittedDate/releasedDate (YYYY-MM-DD format)
  - submittedAtTimestamp/releasedAtTimestamp (Unix timestamps)
  - versionStatus (LATEST_VERSION, REVISED, REVOKED)
  - earliestReleaseDate (config-driven, inherits across versions)
  - submissionId, submitter, groupId, groupName
  - isRevocation, versionComment
  - dataUseTerms, dataUseTermsRestrictedUntil, dataUseTermsUrl
- ✅ Database JOINs:
  - groups_table for groupName
  - data_use_terms_table for dataUseTerms fields
- ✅ Multi-version sequence handling
- ✅ All results match LAPIS exactly

### What Needs Work 🚧
- ❌ Sequence endpoints (nucleotide, amino acid)
- ❌ Sequence decompression with zstandard
- ❌ Alternative output formats (CSV, TSV, FASTA)
- ❌ Insertion endpoints (nucleotideInsertions, aminoAcidInsertions)
- ❌ Integration test suite comparing Querulus vs LAPIS
