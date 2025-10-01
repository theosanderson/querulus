# Querulus

**Direct PostgreSQL-backed LAPIS API replacement**

Querulus is a stateless REST API service that provides LAPIS-compatible endpoints by querying the Loculus PostgreSQL database directly, eliminating the need for the stateful SILO component.

## Architecture

```
OLD: Loculus DB → SILO (in-memory) → LAPIS (REST API)
NEW: Loculus DB → Querulus (stateless REST API)
```

## Status

🎉 **MVP in development** - Basic functionality working!

- ✅ Server running and connecting to PostgreSQL
- ✅ Health checks implemented
- ✅ Basic aggregated endpoint working (`/{organism}/sample/aggregated`)
- ✅ **Verified accuracy: Returns exact same counts as LAPIS (8324 sequences for west-nile)**

## Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL database (localhost:5432)
- Dependencies: `pip install fastapi uvicorn asyncpg sqlalchemy zstandard pydantic pydantic-settings`

### Running

```bash
# Generate config from helm template (one-time setup)
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

# Start server
cd ../..
python -m querulus.main
```

Server will start on `http://localhost:8000`

### Testing

```bash
# Health check
curl http://localhost:8000/health

# Get total count for west-nile
curl http://localhost:8000/west-nile/sample/aggregated

# Compare with LAPIS
curl https://lapis-main.loculus.org/west-nile/sample/aggregated
```

## Configuration

Configuration is loaded from `config/querulus_config.json`, which is generated from the Kubernetes helm template. This ensures Querulus uses the same organism configs and reference genomes as the Loculus backend.

**Environment variables:**

- `QUERULUS_DATABASE_URL` - PostgreSQL connection string (default: `postgresql+asyncpg://postgres:unsecure@localhost:5432/loculus`)
- `QUERULUS_DATABASE_POOL_SIZE` - Connection pool size (default: 20)
- `QUERULUS_CONFIG_PATH` - Path to config file (default: `config/querulus_config.json`)

## API Endpoints

### Implemented ✅

- `GET /` - API information
- `GET /health` - Health check
- `GET /ready` - Readiness check (for Kubernetes)
- `GET /{organism}/sample/aggregated` - Get aggregated sequence counts (basic - no grouping yet)

### In Progress 🚧

- `GET /{organism}/sample/aggregated?fields=...` - Aggregation with field grouping
- `GET /{organism}/sample/details` - Get sequence metadata
- `GET /{organism}/sample/alignedNucleotideSequences/{segment}` - Get nucleotide sequences
- `GET /{organism}/sample/alignedAminoAcidSequences/{gene}` - Get amino acid sequences

### Not Implemented (Phase 1) ❌

Per project scope, mutation-based filtering is excluded from initial release:
- `/sample/nucleotideMutations`
- `/sample/aminoAcidMutations`

## Project Structure

```
querulus/
├── CLAUDE.md                 # Instructions for Claude Code
├── CLAUDE_PROGRESS.md        # Progress tracking and session notes
├── PLAN.md                   # Full technical plan and architecture
├── README.md                 # This file
├── pyproject.toml           # Python dependencies
├── config/
│   ├── README.md            # Config generation instructions
│   └── querulus_config.json # Generated config (gitignored)
└── querulus/
    ├── __init__.py
    ├── main.py              # FastAPI app
    ├── config.py            # Configuration loading
    ├── database.py          # PostgreSQL connection
    └── endpoints/           # (future) Endpoint modules
```

## Development

### Key Technologies

- **FastAPI** - Web framework
- **SQLAlchemy (async)** - Database ORM
- **asyncpg** - Async PostgreSQL driver
- **Zstandard** - Sequence decompression (with dictionary compression)
- **Pydantic** - Data validation

### Database

Querulus queries the `sequence_entries_view` table, which joins:
- `sequence_entries` - Core metadata
- `sequence_entries_preprocessed_data` - Processed sequences and metadata
- `external_metadata_view` - External metadata

All metadata is stored in JSONB fields, requiring specialized query handling.

### Sequence Compression

Sequences are stored compressed using:
- **Algorithm**: Zstandard level 3
- **Dictionary compression**: Uses reference genome sequences as dictionaries
- **Storage format**: Base64-encoded compressed data in JSONB

See `PLAN.md` section 4.2.2 for decompression implementation details.

## Documentation

- **CLAUDE.md** - Instructions for Claude Code (LLM agent)
- **CLAUDE_PROGRESS.md** - Current status, next steps, useful commands
- **PLAN.md** - Comprehensive technical plan and architecture
- **config/README.md** - Configuration generation instructions

## Testing

### Integration Tests

Compare Querulus responses against live LAPIS:

```bash
# Compare counts
echo "LAPIS:" && curl -s https://lapis-main.loculus.org/west-nile/sample/aggregated | jq '.data[0].count'
echo "Querulus:" && curl -s http://localhost:8000/west-nile/sample/aggregated | jq '.data[0].count'
```

### Performance Testing

```bash
# Test query performance
/opt/homebrew/opt/postgresql@15/bin/psql -h localhost -U postgres -d loculus

# Run EXPLAIN ANALYZE
EXPLAIN ANALYZE
SELECT COUNT(*) FROM sequence_entries_view
WHERE organism = 'west-nile' AND released_at IS NOT NULL;
```

## Deployment

### Kubernetes

Querulus is designed to be deployed alongside Loculus:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: querulus
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: querulus
        image: querulus:latest
        ports:
        - containerPort: 8000
        env:
        - name: QUERULUS_DATABASE_URL
          value: "postgresql+asyncpg://postgres:password@postgres-service:5432/loculus"
        volumeMounts:
        - name: config
          mountPath: /app/config
      volumes:
      - name: config
        configMap:
          name: querulus-config
```

The `querulus-config` ConfigMap is generated by helm from `kubernetes/loculus/templates/querulus-config.yaml`.

## Performance

**Target SLOs** (from PLAN.md):

- **Availability**: 99.9% uptime
- **Latency**:
  - P50 < 50ms for aggregated queries
  - P95 < 200ms for aggregated queries
  - P50 < 300ms for sequence queries (100 sequences)
  - P95 < 1s for sequence queries (100 sequences)
- **Throughput**: 100+ req/s per instance
- **Resources**: < 1GB memory per instance

## Contributing

See `CLAUDE.md` for development guidelines and `CLAUDE_PROGRESS.md` for current status and next steps.

## License

[To be determined]
