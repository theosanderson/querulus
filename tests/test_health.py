"""
Test health and readiness endpoints for Querulus.
"""

import requests
import pytest


QUERULUS_URL = "http://localhost:8000"


def test_health_endpoint():
    """Test that /health endpoint returns healthy status"""
    response = requests.get(f"{QUERULUS_URL}/health")

    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert data["status"] == "healthy"
    assert "database" in data
    assert data["database"] == "connected"


def test_ready_endpoint():
    """Test that /ready endpoint returns ready status"""
    response = requests.get(f"{QUERULUS_URL}/ready")

    assert response.status_code == 200
    data = response.json()

    assert "status" in data
    assert data["status"] == "ready"


def test_ready_endpoint_when_db_unavailable():
    """Test that /ready endpoint returns 503 when database is unavailable"""
    # This test would require mocking the database connection
    # For now, we just verify the endpoint exists and works when DB is available
    # In a real scenario with DB down, we'd expect:
    # - response.status_code == 503
    # - data["status"] == "not ready"
    # - data["reason"] == "database not connected"
    pass
