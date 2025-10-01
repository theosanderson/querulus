# Querulus: Direct PostgreSQL-Backed LAPIS Replacement

## Executive Summary

**Querulus** is a greenfield project to create a stateless, database-backed drop-in replacement for the LAPIS API. The current LAPIS+SILO architecture loads all data into memory (SILO) and uses LAPIS as a REST API layer on top. Querulus will query the Loculus PostgreSQL database directly, eliminating the stateful SILO component while maintaining full API compatibility.

**Key Goal**: Same API → Same results, but powered by direct PostgreSQL queries instead of an in-memory columnar store.

**Scope Limitation**: For the initial implementation, we will **NOT** implement mutation/insertion/deletion-based searches. These features will be deferred to a later phase. We also don't need to be able to query endpoints for insertions, mutations, deletions.

---

## 1. System Architecture Overview

### 1.1 Current LAPIS/SILO Architecture

```
┌─────────────┐
│   Loculus   │
│   Backend   │ (Kotlin/Spring)
└──────┬──────┘
       │ get-released-data
       │ (NDJSON stream)
       ▼
┌─────────────┐
│    SILO     │ (C++, in-memory columnar store)
│  Ingestion  │ - Loads all sequence metadata
│             │ - Builds indexes
└──────┬──────┘
       │ HTTP API
       │ (queries)
       ▼
┌─────────────┐
│    LAPIS    │ (Kotlin/Spring REST layer)
│   API       │ - Translates REST → SILO queries
│   Layer     │ - Formats responses (JSON/CSV/FASTA)
└─────────────┘
```

**Problems with this approach**:
- Requires separate stateful service (SILO)
- Must ingest all data into memory
- Data duplication (DB + SILO)
- Complex deployment (3 services)
- Delayed consistency (batch ingestion)

### 1.2 Proposed Querulus Architecture

```
┌─────────────┐
│  Postgres   │ (loculus database)
│   Database  │ - sequence_entries table
│             │ - sequence_entries_preprocessed_data table
│             │ - sequence_entries_view (joins + computed fields)
└──────┬──────┘
       │ Direct SQL queries
       ▼
┌─────────────┐
│  Querulus   │ (New service - language TBD)
│   API       │ - Translates REST → SQL
│   Service   │ - Formats responses (JSON/CSV/FASTA)
│             │ - Stateless!
└─────────────┘
```

**Benefits**:
- Single service, stateless
- No data duplication
- Simpler deployment
- Real-time consistency
- Leverages PostgreSQL query optimization
- Can scale horizontally easily

---

## 2. Database Schema Analysis

### 2.1 Key Tables

Based on schema analysis (`/backend/docs/db/schema.sql`):

#### **sequence_entries**
Core metadata table:
- `accession` (text) - Primary key part 1
- `version` (bigint) - Primary key part 2
- `organism` (text) - Multi-organism support
- `submission_id`, `submitter`, `approver`
- `released_at` (timestamp) - NULL if not released
- `is_revocation` (boolean) - Sequence revocations
- `original_data` (jsonb) - User-submitted metadata
- `submitted_at`, `group_id`

#### **sequence_entries_preprocessed_data**
Processed sequence data:
- `accession`, `version`, `pipeline_version` (composite PK)
- `processed_data` (jsonb) - Contains:
  - `metadata`: Validated/processed fields
  - `alignedNucleotideSequences`: Compressed sequences
  - `alignedAminoAcidSequences`: Per-gene AA sequences
  - `unalignedNucleotideSequences`: Raw sequences
  - `nucleotideInsertions`: Insertion data
  - `aminoAcidInsertions`: Per-gene insertions
- `errors`, `warnings` (jsonb arrays)
- `processing_status` (text): IN_PROCESSING, PROCESSED
- `started_processing_at`, `finished_processing_at`

#### **sequence_entries_view** (Critical!)
Pre-joined view combining:
- Base entry info from `sequence_entries`
- Processed data from `sequence_entries_preprocessed_data`
- External metadata from `external_metadata_view`
- Computed `status` field (RECEIVED, IN_PROCESSING, PROCESSED, APPROVED_FOR_RELEASE)
- Computed `processing_result` (HAS_ERRORS, HAS_WARNINGS, NO_ISSUES)

**This view is the primary query target for Querulus!**

### 2.2 Data Storage Patterns

From actual database query:

```json
{
  "metadata": {
    "length": 2336,
    "authors": "Shulman, L. M.; Bin, H.",
    "lineage": "1A",
    "totalSnps": 7,
    "displayName": "Israel/LOC_0001YTD.1/2001",
    "hostTaxonId": 7157,
    "completeness": 0.2118052407289872,
    "insdcVersion": 1,
    "ncbiSourceDb": "GenBank",
    "geoLocCountry": "Israel",
    "ncbiVirusName": "West Nile virus",
    "ncbiUpdateDate": "2016-07-25",
    "ncbiVirusTaxId": 11082,
    "ncbiReleaseDate": "2010-03-07",
    ...
  },
  "alignedNucleotideSequences": {
    "main": {
      "compressedSequence": "KLUv/WAVKo0B..." // Base64-encoded compressed
    }
  },
  "alignedAminoAcidSequences": {
    "NS1": {"compressedSequence": "..."},
    "env": {"compressedSequence": "..."},
    "prM": {"compressedSequence": "..."},
    "capsid": {"compressedSequence": "..."}
  }
}
```

**Key insight**: All metadata fields are stored in JSONB, using PostgreSQL's native JSON operators for filtering.

---

## 3. LAPIS API Specification

### 3.1 Core Endpoints (To Implement)

From `https://lapis-main.loculus.org/west-nile/api-docs.yaml`:

#### 1. **Aggregated** (`/sample/aggregated`)
- **Purpose**: Count sequences grouped by metadata fields
- **Methods**: GET, POST
- **Formats**: JSON, CSV, TSV
- **Parameters**:
  - `fields`: List of metadata fields to group by (e.g., `country`, `lineage`)
  - Sequence filters: Any metadata field as query param
  - `orderBy`: Sort results
  - `limit`, `offset`: Pagination
- **Response**:
  ```json
  {
    "data": [
      {"country": "USA", "lineage": "1A", "count": 150},
      {"country": "Israel", "lineage": "1A", "count": 42}
    ],
    "info": {
      "dataVersion": "1759331709",
      "requestId": "...",
      "lapisVersion": "0.5.14"
    }
  }
  ```

#### 2. **Details** (`/sample/details`)
- **Purpose**: Get full metadata for matching sequences
- **Methods**: GET, POST
- **Formats**: JSON, CSV, TSV
- **Parameters**:
  - `fields`: List of fields to return (empty = all metadata)
  - Sequence filters
  - `orderBy`, `limit`, `offset`
- **Response**:
  ```json
  {
    "data": [
      {
        "accession": "LOC_00004X1",
        "accessionVersion": "LOC_00004X1.1",
        "authors": "Shirato, K.; Miyoshi, H.",
        "country": "Israel",
        "lineage": "1A",
        "length": 11029,
        ...
      }
    ],
    "info": {...}
  }
  ```

#### 3. **Nucleotide Sequences** (`/sample/alignedNucleotideSequences/main`)
- **Purpose**: Get aligned nucleotide sequences
- **Methods**: GET, POST
- **Formats**: FASTA, JSON, NDJSON
- **Parameters**:
  - Sequence filters
  - `orderBy`, `limit`, `offset`
  - `fastaHeaderTemplate`: Customize FASTA headers
- **Response**: FASTA or JSON with sequences
  - Must decompress `compressedSequence` field
  - Format as FASTA or JSON

#### 4. **Amino Acid Sequences** (`/sample/alignedAminoAcidSequences/{gene}`)
- **Purpose**: Get aligned AA sequences for specific gene
- **Methods**: GET, POST
- **Formats**: FASTA, JSON, NDJSON
- **Gene parameter**: `env`, `NS1`, `capsid`, etc.
- Similar to nucleotide sequences but per-gene

#### 5. **Insertions** (`/sample/nucleotideInsertions`, `/sample/aminoAcidInsertions`)
- **Purpose**: List insertion variants and counts
- **Methods**: GET, POST
- **Formats**: JSON, CSV, TSV
- **Response**: List of insertions with position, sequence, count

### 3.2 Endpoints We WON'T Implement (Phase 1)

Per user request, skipping mutation-related searches:

- ❌ `/sample/nucleotideMutations` - Nucleotide mutation proportions
- ❌ `/sample/aminoAcidMutations` - AA mutation proportions
- ❌ Mutation filter parameters (`nucleotideMutations`, `aminoAcidMutations`)
- ❌ Insertion filter parameters (`nucleotideInsertions`, `aminoAcidInsertions`)

### 3.3 Advanced Endpoints (Defer to Phase 2+)

- ❌ `/sample/phyloSubtree` - Phylogenetic subtree (needs tree data structure)
- ❌ `/sample/mostRecentCommonAncestor` - MRCA queries

---

## 4. Implementation Strategy

### 4.1 Technology Stack Options

#### Option 1: Python + FastAPI
**Pros**:
- Fast development
- Excellent PostgreSQL support (asyncpg, psycopg3)
- Easy JSON handling
- Great for data processing
- FastAPI has good streaming support

**Cons**:
- Slower than compiled languages for CPU-intensive work
- Sequence decompression may be bottleneck

#### Option 2: Kotlin + Spring Boot
**Pros**:
- Match existing LAPIS/Loculus stack
- Code reuse potential from LAPIS
- Good JVM database libraries
- Strong typing

**Cons**:
- More verbose
- Slower development

#### Option 3: Go
**Pros**:
- Fast, compiled
- Good concurrency
- Easy deployment (single binary)
- Excellent PostgreSQL support

**Cons**:
- Less "data science" ecosystem
- More boilerplate

#### Option 4: Rust
**Pros**:
- Maximum performance
- Memory safety
- Excellent PostgreSQL support (sqlx, tokio-postgres)

**Cons**:
- Steepest learning curve
- Slower development

**Recommendation**: **Python + FastAPI** for rapid prototyping, can always rewrite hot paths in Rust later.

### 4.2 Core Components

#### 4.2.1 Query Translation Layer

Map LAPIS query parameters to SQL:

```python
class QueryBuilder:
    def __init__(self, organism: str):
        self.organism = organism
        self.filters = []
        self.fields = []

    def add_metadata_filter(self, field: str, value: Any):
        # Convert to JSONB query
        # WHERE joint_metadata -> 'metadata' ->> 'country' = 'USA'
        self.filters.append(
            f"joint_metadata -> 'metadata' ->> '{field}' = %s"
        )

    def add_date_filter(self, field: str, from_date: str, to_date: str):
        # Date range queries on JSONB fields
        pass

    def build_aggregation_query(self, group_by_fields: List[str]) -> str:
        # Build GROUP BY query with COUNT
        select_fields = [
            f"joint_metadata -> 'metadata' ->> '{field}' AS {field}"
            for field in group_by_fields
        ]
        return f"""
            SELECT {', '.join(select_fields)}, COUNT(*) as count
            FROM sequence_entries_view
            WHERE organism = %s
                AND released_at IS NOT NULL
                {'AND ' + ' AND '.join(self.filters) if self.filters else ''}
            GROUP BY {', '.join(str(i+1) for i in range(len(group_by_fields)))}
        """

    def build_details_query(self, return_fields: List[str]) -> str:
        # Build SELECT query for metadata fields
        pass
```

#### 4.2.2 Sequence Decompression

**From Loculus Backend Analysis** (`CompressionService.kt`): Sequences are compressed using **Zstandard with dictionary compression** for better compression ratios on similar sequences.

**Compression Algorithm**:
1. Uses **Zstandard (zstd)** compression at level 3
2. Uses the **reference genome sequence as a compression dictionary**:
   - For nucleotide sequences: uses the reference nucleotide sequence for that segment (e.g., "main")
   - For amino acid sequences: uses the reference gene sequence (e.g., "env", "NS1")
3. Encodes as Base64 for storage in PostgreSQL JSONB

**Configuration Source**:
- Reference genomes are defined in the backend config
- We'll create a Kubernetes ConfigMap `querulus-config` (similar to `loculus-backend-config`)
- This will generate a JSON file with organism configs including reference genomes

**Decompression Implementation**:

```python
import base64
import zstandard as zstd
import json

class SequenceDecompressor:
    def __init__(self, config_path: str):
        """Load backend config with reference genomes"""
        with open(config_path) as f:
            config = json.load(f)
            self.organisms = config['organisms']

    def decompress_nucleotide_sequence(
        self,
        compressed_b64: str,
        organism: str,
        segment_name: str = 'main'
    ) -> str:
        """Decompress a nucleotide sequence using reference genome as dictionary"""
        ref_genome = self.organisms[organism]['referenceGenome']
        ref_seq = next(
            (seq['sequence'] for seq in ref_genome['nucleotideSequences']
             if seq['name'] == segment_name),
            None
        )
        return self._decompress(compressed_b64, ref_seq)

    def decompress_amino_acid_sequence(
        self,
        compressed_b64: str,
        organism: str,
        gene_name: str
    ) -> str:
        """Decompress an amino acid sequence using reference gene as dictionary"""
        ref_genome = self.organisms[organism]['referenceGenome']
        ref_seq = next(
            (gene['sequence'] for gene in ref_genome['genes']
             if gene['name'] == gene_name),
            None
        )
        return self._decompress(compressed_b64, ref_seq)

    def _decompress(self, compressed_b64: str, dictionary: str | None) -> str:
        """
        Core decompression logic matching Loculus backend (CompressionService.kt)
        """
        # Decode base64
        compressed_bytes = base64.b64decode(compressed_b64)

        # Prepare dictionary if provided
        dict_data = None
        if dictionary:
            dict_data = zstd.ZstdCompressionDict(dictionary.encode('utf-8'))

        # Decompress with dictionary
        dctx = zstd.ZstdDecompressor(dict_data=dict_data)
        decompressed = dctx.decompress(compressed_bytes)

        return decompressed.decode('utf-8')
```

**Kubernetes ConfigMap Setup**:

Create `querulus-config.yaml` in kubernetes templates:

```yaml
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: querulus-config
data:
  querulus_config.json: |
    {{ include "loculus.generateBackendConfig" . | fromYaml | toJson }}
```

This reuses the same backend config generation logic, ensuring consistency with Loculus backend.

**Key Points**:
- Dictionary compression provides ~50-70% better compression for similar sequences
- Without the correct reference genome dictionary, decompression will fail
- Config must be mounted into Querulus container at startup
- Same config format as Loculus backend for compatibility

#### 4.2.3 Response Formatting

Support multiple output formats:

```python
from fastapi import Response
from fastapi.responses import StreamingResponse
import csv
import io

class ResponseFormatter:
    @staticmethod
    def as_json(data: List[dict], info: dict) -> dict:
        return {"data": data, "info": info}

    @staticmethod
    def as_csv(data: List[dict]) -> StreamingResponse:
        # Stream CSV response
        def generate():
            if not data:
                return

            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=data[0].keys())
            writer.writeheader()
            for row in data:
                writer.writerow(row)
                yield output.getvalue()
                output.truncate(0)
                output.seek(0)

        return StreamingResponse(
            generate(),
            media_type="text/csv"
        )

    @staticmethod
    def as_fasta(sequences: List[dict], header_template: str) -> StreamingResponse:
        def generate():
            for seq in sequences:
                header = format_fasta_header(seq, header_template)
                yield f">{header}\n"
                # Wrap at 80 chars
                sequence = seq['sequence']
                for i in range(0, len(sequence), 80):
                    yield sequence[i:i+80] + "\n"

        return StreamingResponse(
            generate(),
            media_type="text/x-fasta"
        )
```

#### 4.2.4 Database Connection Pool

Use connection pooling for performance:

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Async engine
engine = create_async_engine(
    "postgresql+asyncpg://postgres:unsecure@localhost:5432/loculus",
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,  # Verify connections
)

AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Dependency injection
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

### 4.3 Key Challenges & Solutions

#### Challenge 1: JSONB Query Performance

**Problem**: All metadata fields are in JSONB. May be slow for filtering/aggregation.

**Solutions**:
1. Create GIN indexes on frequently queried JSONB paths:
   ```sql
   CREATE INDEX idx_country ON sequence_entries_view
   USING GIN ((joint_metadata -> 'metadata' -> 'country'));

   CREATE INDEX idx_lineage ON sequence_entries_view
   USING GIN ((joint_metadata -> 'metadata' -> 'lineage'));
   ```

2. Consider generated columns for hot-path queries:
   ```sql
   ALTER TABLE sequence_entries_preprocessed_data
   ADD COLUMN country_extracted TEXT
   GENERATED ALWAYS AS (processed_data -> 'metadata' ->> 'geoLocCountry') STORED;

   CREATE INDEX idx_country_extracted ON sequence_entries_preprocessed_data(country_extracted);
   ```

3. Use `EXPLAIN ANALYZE` to optimize queries

#### Challenge 2: Sequence Decompression Performance

**Problem**: Decompressing thousands of sequences may be slow.

**Solutions**:
1. Stream decompression (don't load all into memory)
2. Consider caching frequently accessed sequences (Redis?)
3. Parallel decompression using multiprocessing/asyncio
4. Limit default query size (e.g., max 10,000 sequences)

#### Challenge 3: Matching LAPIS Response Format Exactly

**Problem**: Must match LAPIS JSON structure exactly for drop-in compatibility.

**Solutions**:
1. Write comprehensive integration tests against live LAPIS
2. Compare responses field-by-field
3. Use JSON schema validation
4. Consider using existing LAPIS test suite

#### Challenge 4: Missing Metadata Fields

**Problem**: LAPIS config defines expected metadata fields, but they're dynamic per organism.

**Solutions**:
1. Parse LAPIS database_config.yaml for each organism:
   ```python
   # Load from k8s ConfigMap or file
   with open('database_config.yaml') as f:
       config = yaml.safe_load(f)

   metadata_fields = {
       field['name']: field['type']
       for field in config['schema']['metadata']
   }
   ```

2. Validate query parameters against schema
3. Auto-generate OpenAPI spec from config

---

## 5. Database Schema Insights

### 5.1 Critical Fields in `joint_metadata`

From analysis of sequence_entries_view, the `joint_metadata` JSONB contains:

```json
{
  "metadata": {
    // Numerical/typed fields
    "length": 2336,                    // int
    "totalSnps": 7,                    // int
    "completeness": 0.2118,            // float
    "hostTaxonId": 7157,              // int
    "ncbiVirusTaxId": 11082,          // int

    // String fields
    "lineage": "1A",
    "geoLocCountry": "Israel",
    "ncbiSourceDb": "GenBank",
    "ncbiVirusName": "West Nile virus",
    "hostNameScientific": "Culicidae",
    "insdcAccessionFull": "GU246638.1",

    // Date fields
    "sampleCollectionDate": "2001",
    "ncbiUpdateDate": "2016-07-25",
    "ncbiReleaseDate": "2010-03-07",
    "sampleCollectionDateRangeLower": "2001-01-01",
    "sampleCollectionDateRangeUpper": "2001-12-31",

    // Text fields
    "authors": "Shulman, L. M.; Bin, H.",
    "authorAffiliations": "...",
    "displayName": "Israel/LOC_0001YTD.1/2001"
  },

  "alignedNucleotideSequences": {
    "main": {"compressedSequence": "..."}
  },

  "alignedAminoAcidSequences": {
    "NS1": {"compressedSequence": "..."},
    "env": {"compressedSequence": "..."},
    "prM": {"compressedSequence": "..."},
    "capsid": {"compressedSequence": "..."}
  },

  "nucleotideInsertions": {},
  "aminoAcidInsertions": {},

  "files": {
    "annotations": [...]
  }
}
```

### 5.2 Key Query Patterns

#### Pattern 1: Simple Count
```sql
SELECT COUNT(*)
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL;
```

#### Pattern 2: Aggregation by Country
```sql
SELECT
  joint_metadata -> 'metadata' ->> 'geoLocCountry' AS country,
  COUNT(*) as count
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
GROUP BY country
ORDER BY count DESC;
```

#### Pattern 3: Details with Field Selection
```sql
SELECT
  accession || '.' || version AS accessionVersion,
  joint_metadata -> 'metadata' ->> 'authors' AS authors,
  joint_metadata -> 'metadata' ->> 'geoLocCountry' AS country,
  joint_metadata -> 'metadata' -> 'length' AS length
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
LIMIT 100;
```

#### Pattern 4: Sequence Retrieval
```sql
SELECT
  accession,
  version,
  joint_metadata -> 'metadata' ->> 'displayName' AS displayName,
  joint_metadata -> 'alignedNucleotideSequences' -> 'main' ->> 'compressedSequence' AS sequence
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
  AND joint_metadata -> 'metadata' ->> 'geoLocCountry' = 'Israel'
LIMIT 10;
```

---

## 6. Implementation Phases

### Phase 1: MVP - Core Endpoints (Weeks 1-2)

**Goal**: Implement basic read-only queries without mutations.

**Deliverables**:
1. FastAPI service structure
2. Database connection & query builder
3. `/sample/aggregated` endpoint (JSON only)
4. `/sample/details` endpoint (JSON only)
5. Basic filtering on metadata fields
6. Integration test suite vs live LAPIS

**Success Criteria**:
- Aggregated queries return same counts as LAPIS
- Details queries return same metadata fields

### Phase 2: Sequence Data (Weeks 3-4)

**Deliverables**:
1. Sequence decompression module
2. `/sample/alignedNucleotideSequences/main` (FASTA format)
3. `/sample/alignedAminoAcidSequences/{gene}` (FASTA format)
4. FASTA header template parsing
5. Streaming responses for large result sets

**Success Criteria**:
- Sequences match LAPIS byte-for-byte
- Can handle 10,000+ sequence queries
- Memory usage stays reasonable (< 1GB)

### Phase 3: Output Formats (Week 5)

**Deliverables**:
1. CSV output for aggregated/details
2. TSV output for aggregated/details
3. JSON/NDJSON output for sequences
4. Content negotiation (Accept headers)
5. Compression support (gzip responses)

**Success Criteria**:
- All format outputs match LAPIS exactly

### Phase 4: Advanced Filters & Insertions (Week 6)

**Deliverables**:
1. Date range filtering
2. Numerical comparison operators (>, <, >=, <=)
3. `/sample/nucleotideInsertions` endpoint
4. `/sample/aminoAcidInsertions` endpoint
5. `orderBy` parameter support
6. Pagination (`limit`, `offset`)

**Success Criteria**:
- Complex filter queries work correctly
- Insertion queries match LAPIS

### Phase 5: Production Readiness (Weeks 7-8)

**Deliverables**:
1. OpenAPI documentation (auto-generated)
2. Docker containerization
3. Kubernetes deployment manifests
4. Monitoring & logging (Prometheus metrics)
5. Performance testing & optimization
6. Database index tuning
7. Caching layer (if needed)
8. Error handling & validation

**Success Criteria**:
- Handles 100+ req/s
- P95 latency < 200ms for aggregated queries
- P95 latency < 1s for sequence queries (100 sequences)
- CI/CD pipeline working

### Phase 6: Future (Post-MVP)

**Deferred Features**:
- Mutation-based filtering
- Phylogenetic queries (tree traversal)
- Multi-organism support
- Data versioning/time travel
- GraphQL API?

---

## 7. Testing Strategy

### 7.1 Integration Tests

Compare Querulus vs LAPIS responses:

```python
import pytest
import httpx

LAPIS_BASE = "https://lapis-main.loculus.org"
QUERULUS_BASE = "http://localhost:8000"

@pytest.mark.parametrize("organism", ["west-nile"])
class TestLAPISCompatibility:
    async def test_aggregated_total_count(self, organism):
        lapis_resp = httpx.get(f"{LAPIS_BASE}/{organism}/sample/aggregated")
        querulus_resp = httpx.get(f"{QUERULUS_BASE}/{organism}/sample/aggregated")

        assert lapis_resp.json()["data"][0]["count"] == \
               querulus_resp.json()["data"][0]["count"]

    async def test_aggregated_by_country(self, organism):
        params = {"fields": "country"}
        lapis_resp = httpx.get(f"{LAPIS_BASE}/{organism}/sample/aggregated", params=params)
        querulus_resp = httpx.get(f"{QUERULUS_BASE}/{organism}/sample/aggregated", params=params)

        # Sort and compare counts
        lapis_data = sorted(lapis_resp.json()["data"], key=lambda x: x["country"])
        querulus_data = sorted(querulus_resp.json()["data"], key=lambda x: x["country"])

        assert lapis_data == querulus_data

    async def test_details_fields(self, organism):
        params = {"limit": 10}
        lapis_resp = httpx.get(f"{LAPIS_BASE}/{organism}/sample/details", params=params)
        querulus_resp = httpx.get(f"{QUERULUS_BASE}/{organism}/sample/details", params=params)

        # Compare field names
        lapis_fields = set(lapis_resp.json()["data"][0].keys())
        querulus_fields = set(querulus_resp.json()["data"][0].keys())

        assert lapis_fields == querulus_fields

    async def test_nucleotide_sequences_fasta(self, organism):
        params = {"limit": 5}
        lapis_resp = httpx.get(
            f"{LAPIS_BASE}/{organism}/sample/alignedNucleotideSequences/main",
            params=params,
            headers={"Accept": "text/x-fasta"}
        )
        querulus_resp = httpx.get(
            f"{QUERULUS_BASE}/{organism}/sample/alignedNucleotideSequences/main",
            params=params,
            headers={"Accept": "text/x-fasta"}
        )

        # Parse FASTA and compare sequences (not headers, those may differ in formatting)
        lapis_seqs = parse_fasta(lapis_resp.text)
        querulus_seqs = parse_fasta(querulus_resp.text)

        assert len(lapis_seqs) == len(querulus_seqs)
        for l, q in zip(lapis_seqs, querulus_seqs):
            assert l['sequence'] == q['sequence']
```

### 7.2 Performance Tests

Benchmark query performance:

```python
import time

def benchmark_query(url, params, iterations=100):
    times = []
    for _ in range(iterations):
        start = time.time()
        response = httpx.get(url, params=params)
        times.append(time.time() - start)

    return {
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "p95": statistics.quantiles(times, n=20)[18],
        "p99": statistics.quantiles(times, n=100)[98],
    }

# Run benchmarks
results = {
    "aggregated_simple": benchmark_query(
        f"{QUERULUS_BASE}/west-nile/sample/aggregated",
        {}
    ),
    "aggregated_by_country": benchmark_query(
        f"{QUERULUS_BASE}/west-nile/sample/aggregated",
        {"fields": "country"}
    ),
    "details_100": benchmark_query(
        f"{QUERULUS_BASE}/west-nile/sample/details",
        {"limit": 100}
    ),
}

print(results)
```

---

## 8. Deployment Considerations

### 8.1 Kubernetes Deployment

Replace SILO+LAPIS with single Querulus deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: querulus
spec:
  replicas: 3  # Horizontal scaling!
  selector:
    matchLabels:
      app: querulus
  template:
    metadata:
      labels:
        app: querulus
    spec:
      containers:
      - name: querulus
        image: querulus:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          value: "postgresql://postgres:unsecure@postgres-service:5432/loculus"
        - name: DATABASE_POOL_SIZE
          value: "20"
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: querulus-service
spec:
  selector:
    app: querulus
  ports:
  - port: 80
    targetPort: 8000
  type: ClusterIP
```

### 8.2 Database Connection Management

- Use PgBouncer or built-in connection pooling
- Monitor connection pool saturation
- Configure appropriate pool sizes based on load
- Consider read replicas for horizontal scaling

### 8.3 Caching Strategy

For hot queries (e.g., total counts), consider:

1. **Application-level cache** (Redis):
   - Cache aggregated queries by param hash
   - TTL = 5 minutes (or based on data freshness requirements)

2. **PostgreSQL query cache**:
   - Materialized views for expensive aggregations
   - Refresh periodically or on data change

3. **CDN caching**:
   - Cache static aggregations at CDN level
   - Use ETag/Last-Modified for cache validation

---

## 9. Migration Path

### 9.1 Parallel Deployment

Run Querulus alongside LAPIS for validation:

```
┌─────────────┐
│  Postgres   │
└──────┬──────┘
       │
       ├──────────────┬──────────────┐
       │              │              │
       ▼              ▼              ▼
 ┌──────────┐  ┌──────────┐  ┌──────────┐
 │   SILO   │  │  LAPIS   │  │ Querulus │
 │(existing)│  │(existing)│  │  (new)   │
 └────┬─────┘  └────┬─────┘  └────┬─────┘
      │             │              │
      └─────────────┴──────────────┘
                    │
              ┌─────▼─────┐
              │   Nginx   │  (A/B testing)
              │  Routing  │
              └───────────┘
```

Use Nginx/K8s routing to split traffic:
- 95% → LAPIS (existing)
- 5% → Querulus (new)

Monitor error rates, compare responses.

### 9.2 Cutover Plan

1. **Week 1-2**: Deploy Querulus in shadow mode (no traffic)
2. **Week 3-4**: 5% traffic to Querulus, monitor
3. **Week 5**: 25% traffic
4. **Week 6**: 50% traffic
5. **Week 7**: 100% traffic to Querulus
6. **Week 8**: Decommission SILO+LAPIS

Rollback plan: Revert traffic routing to LAPIS.

---

## 10. Open Questions & Risks

### 10.1 Open Questions

1. **Sequence compression algorithm**: What exact algorithm does SILO use for `compressedSequence`?
   - Need to examine SILO source code or reverse-engineer
   - Alternatively, store uncompressed in Querulus and compress on-the-fly

2. **LAPIS config location**: Where does LAPIS get `database_config.yaml` in production?
   - Kubernetes ConfigMap: `lapis-silo-database-config-{organism}`
   - Need to parse this in Querulus

3. **Data versioning**: Does LAPIS have any data versioning/ETag logic?
   - Yes, uses `dataVersion` header from SILO
   - Querulus should use `table_update_tracker` table or DB timestamp

4. **Authentication**: Does LAPIS have auth?
   - Appears to support access keys for protected data
   - Querulus may need to implement same logic

5. **Multi-organism routing**: How to handle multiple organisms?
   - LAPIS uses path prefix: `/{organism}/sample/...`
   - Querulus needs same routing logic

### 10.2 Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Query performance too slow | High | Medium | Add indexes, use materialized views, caching |
| Sequence decompression bottleneck | High | Medium | Parallel processing, limit result sizes |
| API incompatibility breaks clients | High | Low | Comprehensive integration tests |
| PostgreSQL connection pool exhaustion | Medium | Medium | PgBouncer, monitoring, auto-scaling |
| JSONB query performance | High | Low | GIN indexes, generated columns |
| Unknown LAPIS features discovered late | Medium | Low | Deep dive into LAPIS source code early |

---

## 11. Success Metrics

### 11.1 Functional Metrics

- [ ] 100% of core endpoints implemented
- [ ] 95%+ response compatibility with LAPIS (field names, structure)
- [ ] Aggregated query counts match LAPIS exactly
- [ ] Sequence data matches LAPIS byte-for-byte

### 11.2 Performance Metrics

Target SLOs:

- **Availability**: 99.9% uptime
- **Latency**:
  - P50 < 50ms for aggregated queries
  - P95 < 200ms for aggregated queries
  - P99 < 500ms for aggregated queries
  - P50 < 100ms for details queries (100 records)
  - P95 < 500ms for details queries (100 records)
  - P50 < 300ms for sequence queries (100 sequences)
  - P95 < 1s for sequence queries (100 sequences)
- **Throughput**: 100+ req/s per instance
- **Error rate**: < 0.1%

### 11.3 Resource Metrics

- **Memory**: < 1GB per instance under normal load
- **CPU**: < 50% average utilization
- **Database connections**: < 50% of pool size

---

## 12. Next Steps

1. **Immediate (Day 1)**:
   - Set up Python development environment
   - Create FastAPI skeleton project
   - Establish database connection to localhost:5432
   - Write first query: `SELECT COUNT(*) FROM sequence_entries_view WHERE organism='west-nile'`

2. **Week 1**:
   - Implement QueryBuilder class
   - Implement `/sample/aggregated` endpoint (JSON)
   - Write integration tests vs LAPIS
   - Set up CI/CD pipeline

3. **Week 2**:
   - Implement `/sample/details` endpoint
   - Add metadata filtering logic
   - Optimize queries with EXPLAIN ANALYZE
   - Add GIN indexes

4. **Ongoing**:
   - Regular testing against LAPIS production
   - Performance benchmarking
   - Code review with team
   - Documentation

---

## Appendix A: Example Queries

### Count all west-nile sequences
```sql
SELECT COUNT(*)
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL;
-- Result: 8324
```

### Group by country
```sql
SELECT
  joint_metadata -> 'metadata' ->> 'geoLocCountry' AS country,
  COUNT(*) as count
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
GROUP BY country
ORDER BY count DESC
LIMIT 10;
```

### Filter by country and lineage
```sql
SELECT
  accession || '.' || version AS accessionVersion,
  joint_metadata -> 'metadata' ->> 'displayName' AS displayName,
  joint_metadata -> 'metadata' ->> 'geoLocCountry' AS country,
  joint_metadata -> 'metadata' ->> 'lineage' AS lineage
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
  AND joint_metadata -> 'metadata' ->> 'geoLocCountry' = 'USA'
  AND joint_metadata -> 'metadata' ->> 'lineage' = '1A'
LIMIT 10;
```

### Get sequences for FASTA export
```sql
SELECT
  accession || '.' || version AS accessionVersion,
  joint_metadata -> 'metadata' ->> 'displayName' AS displayName,
  joint_metadata -> 'alignedNucleotideSequences' -> 'main' ->> 'compressedSequence' AS sequence
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
LIMIT 100;
```

---

## Appendix B: LAPIS Response Format Examples

### Aggregated Response
```json
{
  "data": [
    {
      "count": 8324
    }
  ],
  "info": {
    "dataVersion": "1759331709",
    "requestId": "fcf80329-a3b8-47d7-b53d-6d33948b70c8",
    "requestInfo": "West Nile Virus on lapis-main.loculus.org at 2025-10-01T22:20:10.230020706",
    "reportTo": "Please report to https://github.com/GenSpectrum/LAPIS/issues...",
    "lapisVersion": "0.5.14",
    "siloVersion": "0f7b540400137bf7f605c6e382e9d4e213991695"
  }
}
```

### Details Response (fields: authors, country, lineage)
```json
{
  "data": [
    {
      "authors": "Shirato, K.; Miyoshi, H.; Goto, A.; et al.",
      "country": "Israel",
      "lineage": "1A"
    },
    {
      "authors": "Chen, W. J.; Dong, C. F.; et al.",
      "country": "USA",
      "lineage": "1A"
    }
  ],
  "info": {
    "dataVersion": "1759331709",
    "requestId": "...",
    "lapisVersion": "0.5.14"
  }
}
```

---

## Conclusion

Querulus represents a significant architectural simplification of the LAPIS API stack. By eliminating the stateful SILO component and querying PostgreSQL directly, we can achieve:

- **Simpler deployment**: 3 services → 1 service
- **Real-time data**: No ingestion lag
- **Easier scaling**: Stateless horizontal scaling
- **Lower operational complexity**: No SILO state management

The main technical challenges are:

1. **Performance**: Ensuring JSONB queries are fast enough
2. **Compatibility**: Matching LAPIS API exactly
3. **Decompression**: Efficiently handling sequence data

All of these are solvable with proper indexing, caching, and optimization.

The project is feasible and should deliver a production-ready system in 8 weeks, with an MVP in 4 weeks.

**Recommendation**: Proceed with Python + FastAPI implementation, focusing on core endpoints first (aggregated, details), then adding sequence support and output formats incrementally.