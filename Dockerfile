# Multi-stage build for Querulus
FROM python:3.12-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy package files
COPY pyproject.toml /tmp/
COPY querulus/ /tmp/querulus/
WORKDIR /tmp

# Install package with dependencies
RUN pip install --no-cache-dir .

# Final stage
FROM python:3.12-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create app directory
WORKDIR /app

# Copy application code
COPY querulus/ /app/querulus/
COPY config/ /app/config/

# Create non-root user
RUN useradd -m -u 1000 querulus && \
    chown -R querulus:querulus /app
USER querulus

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "querulus.main:app", "--host", "0.0.0.0", "--port", "8000"]
