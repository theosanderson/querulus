# Instructions for Claude Code

## First Steps in Every Session

1. **Read these files in order**:
   - This file (`CLAUDE.md`) - General instructions
   - `CLAUDE_PROGRESS.md` - Current status and next steps
   - `PLAN.md` - Full technical plan and architecture (refer to as needed)

2. **Understand the context**:
   - Project: **Querulus** - Direct PostgreSQL-backed replacement for LAPIS API
   - Goal: Drop-in replacement for LAPIS that queries PostgreSQL directly instead of using stateful SILO
   - Stack: Python + FastAPI + PostgreSQL
   - Status: Check CLAUDE_PROGRESS.md for current phase

3. **Before starting work**:
   - Review "Next Steps" in CLAUDE_PROGRESS.md
   - Check for any "Active Blockers"
   - Ask user if continuing from last session or starting something new

---

## Project Overview

### What is Querulus?

Querulus replaces the LAPIS+SILO architecture with a stateless service that queries the Loculus PostgreSQL database directly:

```
OLD: Loculus DB → SILO (in-memory) → LAPIS (REST API)
NEW: Loculus DB → Querulus (stateless REST API)
```

### Key Technical Points

1. **Database**:
   - Main table: `sequence_entries_view`
   - All metadata in JSONB: `joint_metadata -> 'metadata' ->> 'fieldName'`
   - Sequences stored compressed with Zstandard + dictionary compression
   - Connection: `localhost:5432`, database=`loculus`, user=`postgres`, password=`unsecure`

2. **Compression**:
   - Sequences use Zstandard with **dictionary compression**
   - Dictionary = reference genome sequence
   - Must load reference genomes from config to decompress
   - See `CompressionService.kt` in Loculus backend for algorithm

3. **API Compatibility**:
   - Must match LAPIS API exactly for drop-in replacement
   - Test against: https://lapis-main.loculus.org/west-nile/
   - Response format must be identical (field names, structure, etc.)

4. **Scope**:
   - ✅ Implement: aggregated, details, sequences, insertions endpoints
   - ❌ Skip (Phase 1): mutation-based filtering, phylogenetic queries

---

## Working with This Codebase

### Git Repository

This project uses git for version control. **You should commit your work frequently** - commit as often as you like! Good times to commit:

- After completing a logical unit of work (e.g., finishing a module)
- Before making experimental changes
- When switching between tasks
- At the end of each session

Use clear, descriptive commit messages:
```bash
git add .
git commit -m "Add sequence decompression module with zstd dictionary support"
```

Don't worry about making too many commits - frequent commits make it easier to track progress and roll back if needed.

### Project Structure

```
querulus/
├── CLAUDE.md                 # This file - read first
├── CLAUDE_PROGRESS.md        # Current status - read second
├── PLAN.md                   # Full technical plan - reference as needed
├── kubernetes/               # Will contain querulus-config.yaml
│   └── loculus/
│       └── templates/
└── querulus/                 # Python package (to be created)
    ├── main.py              # FastAPI app
    ├── config.py            # Load reference genomes & config
    ├── database.py          # PostgreSQL connection
    ├── models.py            # Request/response models
    ├── compression.py       # Sequence decompression
    └── endpoints/
        ├── aggregated.py
        ├── details.py
        └── sequences.py
```

### Key Files in Existing Codebase

- `loculus/backend/src/main/kotlin/org/loculus/backend/service/submission/CompressionService.kt` - Reference for decompression algorithm
- `loculus/backend/docs/db/schema.sql` - Database schema documentation
- `loculus/kubernetes/loculus/templates/loculus-backend-config.yaml` - Config generation to reuse

---

## Development Guidelines

### Code Style

- **Python**: Follow PEP 8, use type hints, async/await patterns
- **Concise**: Keep functions focused and small
- **Comments**: Document WHY not WHAT (code should be self-explanatory)
- **Testing**: Write integration tests comparing against live LAPIS

### Database Queries

- **Always filter** on `released_at IS NOT NULL` for released sequences
- **Always filter** on `organism = ?` for multi-organism support
- **Use JSONB operators** for metadata filtering:
  - `joint_metadata -> 'metadata' ->> 'country'` for string access
  - `joint_metadata -> 'metadata' -> 'length'` for numeric access
- **Test with EXPLAIN ANALYZE** to check query performance
- **Add indexes** as needed (GIN indexes for JSONB, btree for common filters)

### Testing Strategy

1. **Integration tests**: Compare Querulus output vs LAPIS output
2. **Performance tests**: Measure latency and throughput
3. **Correctness**: Exact match on counts, field names, data structure

Example test pattern:
```python
# Test that aggregated counts match
lapis_count = requests.get("https://lapis-main.loculus.org/west-nile/sample/aggregated").json()["data"][0]["count"]
querulus_count = requests.get("http://localhost:8000/west-nile/sample/aggregated").json()["data"][0]["count"]
assert lapis_count == querulus_count
```

---

## Common Tasks & How-To

### Starting Development Server

```bash
cd querulus
python -m uvicorn main:app --reload --port 8000
```

### Connecting to Database

```bash
/opt/homebrew/opt/postgresql@15/bin/psql -h localhost -U postgres -d loculus
```

### Testing Against LAPIS

```bash
# Get aggregated count
curl https://lapis-main.loculus.org/west-nile/sample/aggregated | jq

# Get details
curl https://lapis-main.loculus.org/west-nile/sample/details?limit=5 | jq

# Compare with Querulus
curl http://localhost:8000/west-nile/sample/aggregated | jq
```

### Generating Kubernetes Config

```bash
cd kubernetes
helm template loculus . --set environment=main > /tmp/output.yaml
grep -A 50 "kind: ConfigMap" /tmp/output.yaml | grep -A 50 "loculus-backend-config"
```

### Decompressing a Sequence (for testing)

See `PLAN.md` section 4.2.2 for full implementation.

---

## Critical Success Factors

### Must-Haves

1. **API Compatibility**: Responses must match LAPIS exactly
2. **Performance**: Must handle 100+ req/s with reasonable latency
3. **Correctness**: Aggregated counts must match LAPIS
4. **Decompression**: Must correctly decompress sequences with dictionary

### Nice-to-Haves

- Caching layer for hot queries
- Extensive logging and monitoring
- Admin/debug endpoints
- GraphQL API

---

## Things to Watch Out For

### Common Pitfalls

1. **Missing dictionary for decompression**:
   - Sequences won't decompress without reference genome
   - Must load config with reference genomes at startup

2. **JSONB type handling**:
   - Strings: use `->>`
   - Numbers: use `->` and cast: `(joint_metadata -> 'metadata' -> 'length')::int`
   - Dates: may need parsing and casting

3. **Released vs unreleased sequences**:
   - Always filter `released_at IS NOT NULL` unless specifically querying unreleased
   - LAPIS only shows released sequences

4. **Organism filtering**:
   - Multi-organism database, must filter by organism
   - Extract from URL path: `/{organism}/sample/...`

5. **Response format details**:
   - Must include `info` object with `dataVersion`, `requestId`, `lapisVersion`
   - Field names must match exactly (case-sensitive)
   - Empty arrays vs null (check LAPIS behavior)

### Performance Traps

- **N+1 queries**: Batch sequence decompression
- **Large result sets**: Stream responses, don't load all into memory
- **Missing indexes**: Monitor slow queries, add indexes as needed
- **Connection pool exhaustion**: Configure appropriate pool size

---

## Communication Guidelines

### With User

- **Be concise**: User values brief, direct responses
- **Show progress**: Use TodoWrite tool for multi-step tasks
- **Ask when uncertain**: Better to clarify than assume
- **Explain decisions**: When making technical choices, briefly explain why

### Updating Documentation

- **CLAUDE_PROGRESS.md**: Update at end of each session
  - Mark completed tasks
  - Update "Next Steps"
  - Document decisions made
  - Note any blockers

- **Design Decisions Log**: Add entry when making significant choices
  - Date the decision
  - Explain rationale
  - Note alternatives considered

---

## Debugging Tips

### Database Issues

```sql
-- Check if sequences exist
SELECT organism, COUNT(*)
FROM sequence_entries_view
WHERE released_at IS NOT NULL
GROUP BY organism;

-- Inspect JSONB structure
SELECT
  accession,
  jsonb_pretty(joint_metadata -> 'metadata')
FROM sequence_entries_view
WHERE organism = 'west-nile'
LIMIT 1;

-- Check compression
SELECT
  accession,
  length(joint_metadata -> 'alignedNucleotideSequences' -> 'main' ->> 'compressedSequence') as compressed_length
FROM sequence_entries_view
WHERE organism = 'west-nile'
  AND released_at IS NOT NULL
LIMIT 5;
```

### FastAPI Issues

- Check `/docs` for auto-generated API documentation
- Use `--reload` flag for development
- Set `DEBUG=true` for detailed error messages
- Use `@app.on_event("startup")` for config loading

### Decompression Issues

- Verify reference genome is loaded correctly
- Check Base64 decoding doesn't fail
- Ensure dictionary is passed to Zstd decompressor
- Test with a single sequence first before batch processing

---

## Resources

### Documentation

- **LAPIS API**: https://lapis-main.loculus.org/west-nile/api-docs.yaml
- **FastAPI**: https://fastapi.tiangolo.com/
- **SQLAlchemy Async**: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- **Zstandard Python**: https://pypi.org/project/zstandard/

### Key Endpoints for Testing

- Aggregated: `https://lapis-main.loculus.org/west-nile/sample/aggregated`
- Details: `https://lapis-main.loculus.org/west-nile/sample/details?limit=10`
- Sequences: `https://lapis-main.loculus.org/west-nile/sample/alignedNucleotideSequences/main?limit=5`

### Codebase References

- Compression logic: `loculus/backend/src/main/kotlin/org/loculus/backend/service/submission/CompressionService.kt`
- Database schema: `loculus/backend/docs/db/schema.sql`
- Backend config: `loculus/kubernetes/loculus/templates/loculus-backend-config.yaml`

---

## When You're Stuck

1. **Check CLAUDE_PROGRESS.md** - Is this a known blocker?
2. **Review PLAN.md** - Is this covered in the architecture?
3. **Look at Loculus code** - How does the backend handle this?
4. **Test against LAPIS** - What does the reference implementation do?
5. **Ask the user** - They know the domain and requirements

---

## Success Metrics

You're on track if:

- ✅ Aggregated queries return same counts as LAPIS
- ✅ Details queries return same metadata fields as LAPIS
- ✅ Sequences decompress correctly and match LAPIS output
- ✅ Response JSON structure matches LAPIS exactly
- ✅ Query latency is reasonable (< 1s for most queries)
- ✅ No database connection errors or memory leaks
- ✅ Code is clean, typed, and testable

---

## Final Reminders

- **Read CLAUDE_PROGRESS.md first** - It has your current status and next steps
- **Update CLAUDE_PROGRESS.md last** - Document what you did and what's next
- **Reference PLAN.md as needed** - Don't try to keep everything in memory
- **Test against LAPIS frequently** - Compatibility is critical
- **Ask questions early** - Better to clarify requirements than build wrong thing
- **Keep it simple** - Start with working code, optimize later

Good luck! 🚀
