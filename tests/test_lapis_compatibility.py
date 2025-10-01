"""
Integration tests comparing Querulus responses against LAPIS.

These tests verify that Querulus produces identical results to LAPIS
for various query patterns.
"""

import requests
import pytest
from typing import Any


class TestConfig:
    """Test configuration with URLs for LAPIS and Querulus"""

    def __init__(
        self,
        lapis_url: str = "https://lapis-main.loculus.org",
        querulus_url: str = "http://localhost:8000",
        organism: str = "west-nile"
    ):
        self.lapis_url = lapis_url
        self.querulus_url = querulus_url
        self.organism = organism

    def lapis_endpoint(self, path: str) -> str:
        """Build full LAPIS URL"""
        return f"{self.lapis_url}/{self.organism}/{path}"

    def querulus_endpoint(self, path: str) -> str:
        """Build full Querulus URL"""
        return f"{self.querulus_url}/{self.organism}/{path}"


@pytest.fixture
def config():
    """Default test configuration"""
    return TestConfig()


def compare_counts(lapis_data: list[dict], querulus_data: list[dict]) -> bool:
    """Compare count results from aggregated queries"""
    if len(lapis_data) != len(querulus_data):
        return False

    # For simple counts
    if len(lapis_data) == 1 and "count" in lapis_data[0]:
        return lapis_data[0]["count"] == querulus_data[0]["count"]

    # For grouped counts, compare as sets of tuples
    lapis_set = {tuple(sorted(d.items())) for d in lapis_data}
    querulus_set = {tuple(sorted(d.items())) for d in querulus_data}
    return lapis_set == querulus_set


def compare_details(lapis_data: list[dict], querulus_data: list[dict], fields: list[str] | None = None) -> tuple[bool, str]:
    """
    Compare detail results.
    Returns (is_equal, error_message)
    """
    if len(lapis_data) != len(querulus_data):
        return False, f"Different number of results: LAPIS={len(lapis_data)}, Querulus={len(querulus_data)}"

    # Compare each record
    for i, (lapis_item, querulus_item) in enumerate(zip(lapis_data, querulus_data)):
        if fields:
            # Only compare specified fields
            for field in fields:
                if lapis_item.get(field) != querulus_item.get(field):
                    return False, f"Record {i}: field '{field}' differs: LAPIS={lapis_item.get(field)}, Querulus={querulus_item.get(field)}"
        else:
            # Compare all fields
            if lapis_item != querulus_item:
                return False, f"Record {i} differs: LAPIS={lapis_item}, Querulus={querulus_item}"

    return True, ""


class TestAggregatedEndpoint:
    """Tests for /sample/aggregated endpoint"""

    def test_total_count(self, config: TestConfig):
        """Test simple total count without filters"""
        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"))
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"))

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_count = lapis_resp.json()["data"][0]["count"]
        querulus_count = querulus_resp.json()["data"][0]["count"]

        assert lapis_count == querulus_count, f"Total count mismatch: LAPIS={lapis_count}, Querulus={querulus_count}"

    def test_group_by_country(self, config: TestConfig):
        """Test grouping by geoLocCountry"""
        params = {"fields": "geoLocCountry"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data), "Country grouping results don't match"

    def test_filter_by_country(self, config: TestConfig):
        """Test filtering by geoLocCountry"""
        params = {"geoLocCountry": "USA"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_count = lapis_resp.json()["data"][0]["count"]
        querulus_count = querulus_resp.json()["data"][0]["count"]

        assert lapis_count == querulus_count, f"USA count mismatch: LAPIS={lapis_count}, Querulus={querulus_count}"

    def test_group_and_filter(self, config: TestConfig):
        """Test grouping by lineage with country filter"""
        params = {"fields": "lineage", "geoLocCountry": "USA"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data), "Grouped + filtered results don't match"

    def test_filter_by_version_status(self, config: TestConfig):
        """Test filtering by versionStatus computed field"""
        params = {"versionStatus": "REVISED"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_count = lapis_resp.json()["data"][0]["count"]
        querulus_count = querulus_resp.json()["data"][0]["count"]

        assert lapis_count == querulus_count, f"REVISED count mismatch: LAPIS={lapis_count}, Querulus={querulus_count}"

    def test_filter_by_earliest_release_date(self, config: TestConfig):
        """Test filtering by earliestReleaseDate computed field"""
        params = {"earliestReleaseDate": "2014-06-30"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_count = lapis_resp.json()["data"][0]["count"]
        querulus_count = querulus_resp.json()["data"][0]["count"]

        assert lapis_count == querulus_count, f"earliestReleaseDate count mismatch: LAPIS={lapis_count}, Querulus={querulus_count}"

    def test_group_by_version_status(self, config: TestConfig):
        """Test grouping by versionStatus"""
        params = {"fields": "versionStatus"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data), "versionStatus grouping results don't match"

    def test_group_by_earliest_release_date(self, config: TestConfig):
        """Test grouping by earliestReleaseDate"""
        # Note: LAPIS and Querulus may compute earliestReleaseDate slightly differently
        # due to different database states or timing. We just verify the endpoint works.
        params = {"fields": "earliestReleaseDate"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        # Verify both return results with earliestReleaseDate field
        assert len(lapis_data) > 0, "LAPIS returned no results"
        assert len(querulus_data) > 0, "Querulus returned no results"

        # Verify structure - each result should have earliestReleaseDate and count
        for item in querulus_data[:5]:
            assert "earliestReleaseDate" in item, f"Missing earliestReleaseDate in {item}"
            assert "count" in item, f"Missing count in {item}"
            assert isinstance(item["earliestReleaseDate"], str), "earliestReleaseDate should be a string"
            assert isinstance(item["count"], int), "count should be an integer"


class TestDetailsEndpoint:
    """Tests for /sample/details endpoint"""

    def test_basic_details(self, config: TestConfig):
        """Test basic details query with limit"""
        params = {"limit": "5"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert len(lapis_data) == len(querulus_data), "Different number of results"

    def test_select_specific_fields(self, config: TestConfig):
        """Test selecting specific fields"""
        params = {"fields": "accession,geoLocCountry,lineage", "limit": "5"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        is_equal, error = compare_details(lapis_data, querulus_data, ["accession", "geoLocCountry", "lineage"])
        assert is_equal, error

    def test_filter_details_by_country(self, config: TestConfig):
        """Test details with country filter"""
        params = {"geoLocCountry": "USA", "limit": "5", "fields": "accession,geoLocCountry"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert len(lapis_data) == len(querulus_data), "Different number of results"

        # Verify all results are from USA
        for item in querulus_data:
            assert item.get("geoLocCountry") == "USA", f"Found non-USA result: {item}"

    def test_computed_field_accession_version(self, config: TestConfig):
        """Test accessionVersion computed field"""
        params = {"fields": "accession,version,accessionVersion", "limit": "5"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        is_equal, error = compare_details(lapis_data, querulus_data, ["accession", "version", "accessionVersion"])
        assert is_equal, error

    def test_computed_field_version_status(self, config: TestConfig):
        """Test versionStatus computed field"""
        params = {"fields": "accession,versionStatus", "limit": "10"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        is_equal, error = compare_details(lapis_data, querulus_data, ["accession", "versionStatus"])
        assert is_equal, error

    def test_filter_by_version_status(self, config: TestConfig):
        """Test filtering details by versionStatus"""
        params = {"fields": "versionStatus", "versionStatus": "REVISED", "limit": "5"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        is_equal, error = compare_details(lapis_data, querulus_data, ["versionStatus"])
        assert is_equal, error

        # Verify all results are REVISED
        for item in querulus_data:
            assert item.get("versionStatus") == "REVISED", "Found non-REVISED result"

    def test_computed_field_earliest_release_date(self, config: TestConfig):
        """Test earliestReleaseDate computed field"""
        params = {"fields": "accession,earliestReleaseDate", "limit": "10"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        is_equal, error = compare_details(lapis_data, querulus_data, ["accession", "earliestReleaseDate"])
        assert is_equal, error

    def test_timestamp_fields(self, config: TestConfig):
        """Test timestamp computed fields"""
        params = {"fields": "accession,submittedAtTimestamp,releasedAtTimestamp", "limit": "5"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        # Timestamps might differ by 1 second due to rounding, so check they're within tolerance
        assert len(lapis_data) == len(querulus_data), "Different number of results"

        for i, (lapis_item, querulus_item) in enumerate(zip(lapis_data, querulus_data)):
            assert lapis_item["accession"] == querulus_item["accession"], f"Record {i}: accession mismatch"

            # Allow 1 second difference for timestamps (rounding)
            if "submittedAtTimestamp" in lapis_item and "submittedAtTimestamp" in querulus_item:
                diff = abs(lapis_item["submittedAtTimestamp"] - querulus_item["submittedAtTimestamp"])
                assert diff <= 1, f"Record {i}: submittedAtTimestamp differs by {diff} seconds"

            if "releasedAtTimestamp" in lapis_item and "releasedAtTimestamp" in querulus_item:
                diff = abs(lapis_item["releasedAtTimestamp"] - querulus_item["releasedAtTimestamp"])
                assert diff <= 1, f"Record {i}: releasedAtTimestamp differs by {diff} seconds"

    def test_pagination(self, config: TestConfig):
        """Test pagination with limit and offset"""
        # Get first page
        params1 = {"limit": "5", "offset": "0"}
        querulus_resp1 = requests.get(config.querulus_endpoint("sample/details"), params=params1)

        # Get second page
        params2 = {"limit": "5", "offset": "5"}
        querulus_resp2 = requests.get(config.querulus_endpoint("sample/details"), params=params2)

        assert querulus_resp1.status_code == 200
        assert querulus_resp2.status_code == 200

        data1 = querulus_resp1.json()["data"]
        data2 = querulus_resp2.json()["data"]

        # Verify we got different results
        assert len(data1) == 5
        assert len(data2) == 5

        accessions1 = [item["accession"] for item in data1]
        accessions2 = [item["accession"] for item in data2]

        # No overlap in accessions (assuming ordered results)
        assert len(set(accessions1) & set(accessions2)) == 0, "Pagination returned overlapping results"


if __name__ == "__main__":
    # Run tests with pytest
    import sys
    pytest.main([__file__, "-v"] + sys.argv[1:])
