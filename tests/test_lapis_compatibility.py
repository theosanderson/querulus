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
        params = {"fields": "earliestReleaseDate"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        # Results may be in different order, so compare as sets
        assert compare_counts(lapis_data, querulus_data), "earliestReleaseDate grouping results don't match"


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


class TestSequenceEndpoints:
    """Tests for sequence endpoints"""

    def test_nucleotide_sequences_basic(self, config: TestConfig):
        """Test basic nucleotide sequences query"""
        params = {"limit": "2"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/alignedNucleotideSequences"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/alignedNucleotideSequences"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_fasta = lapis_resp.text
        querulus_fasta = querulus_resp.text

        # Extract headers for comparison
        lapis_headers = [line for line in lapis_fasta.split('\n') if line.startswith('>')]
        querulus_headers = [line for line in querulus_fasta.split('\n') if line.startswith('>')]

        assert len(lapis_headers) == len(querulus_headers), "Different number of sequences"
        assert lapis_headers == querulus_headers, "Different sequence headers"

        # Extract sequences for comparison
        lapis_seqs = []
        querulus_seqs = []

        for fasta_text, seq_list in [(lapis_fasta, lapis_seqs), (querulus_fasta, querulus_seqs)]:
            current_seq = []
            for line in fasta_text.split('\n'):
                if line.startswith('>'):
                    if current_seq:
                        seq_list.append(''.join(current_seq))
                        current_seq = []
                elif line.strip():
                    current_seq.append(line.strip())
            if current_seq:
                seq_list.append(''.join(current_seq))

        assert lapis_seqs == querulus_seqs, "Sequences don't match"

    def test_nucleotide_sequences_with_filter(self, config: TestConfig):
        """Test nucleotide sequences with country filter"""
        params = {"geoLocCountry": "USA", "limit": "3"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/alignedNucleotideSequences"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/alignedNucleotideSequences"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_headers = [line for line in lapis_resp.text.split('\n') if line.startswith('>')]
        querulus_headers = [line for line in querulus_resp.text.split('\n') if line.startswith('>')]

        assert lapis_headers == querulus_headers, "Filtered sequences don't match"


class TestOrderBy:
    """Tests for orderBy parameter"""

    def test_details_order_by_accession(self, config: TestConfig):
        """Test orderBy=accession on details endpoint"""
        params = {"limit": "10", "orderBy": "accession"}

        resp = requests.get(config.querulus_endpoint("sample/details"), params=params)
        assert resp.status_code == 200

        data = resp.json()["data"]
        accessions = [item["accession"] for item in data]

        # Verify it's sorted
        assert accessions == sorted(accessions), "Results not sorted by accession"

    def test_details_order_by_metadata_field(self, config: TestConfig):
        """Test orderBy with metadata field"""
        params = {"limit": "20", "orderBy": "geoLocCountry", "fields": "accession,geoLocCountry"}

        resp = requests.get(config.querulus_endpoint("sample/details"), params=params)
        assert resp.status_code == 200

        data = resp.json()["data"]
        countries = [item.get("geoLocCountry") for item in data]

        # Verify it's sorted (None values first in SQL)
        assert countries == sorted(countries, key=lambda x: (x is not None, x)), "Results not sorted by country"

    def test_aggregated_order_by_field(self, config: TestConfig):
        """Test orderBy on aggregated endpoint"""
        params = {"fields": "geoLocCountry", "limit": "10", "orderBy": "geoLocCountry"}

        resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)
        assert resp.status_code == 200

        data = resp.json()["data"]
        countries = [item["geoLocCountry"] for item in data]

        # Verify it's sorted
        assert countries == sorted(countries, key=lambda x: (x is not None, x)), "Aggregated results not sorted"

    def test_details_order_by_random(self, config: TestConfig):
        """Test orderBy=random returns different results"""
        params = {"limit": "5", "orderBy": "random"}

        resp1 = requests.get(config.querulus_endpoint("sample/details"), params=params)
        resp2 = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        accessions1 = [item["accession"] for item in resp1.json()["data"]]
        accessions2 = [item["accession"] for item in resp2.json()["data"]]

        # Random ordering should give different results (very unlikely to be the same)
        assert accessions1 != accessions2, "Random ordering returned same results twice"


class TestRangeQueries:
    """Test range query support for numeric and date fields"""

    def test_int_range_both_bounds(self, config: TestConfig):
        """Test integer range with both From and To"""
        params = {"lengthFrom": "10000", "lengthTo": "11000"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data)

    def test_int_range_only_from(self, config: TestConfig):
        """Test integer range with only From (>=)"""
        params = {"lengthFrom": "11000"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data)

    def test_int_range_only_to(self, config: TestConfig):
        """Test integer range with only To (<=)"""
        params = {"lengthTo": "9000"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data)

    def test_date_range_both_bounds(self, config: TestConfig):
        """Test date range with both From and To"""
        params = {"ncbiReleaseDateFrom": "2010-01-01", "ncbiReleaseDateTo": "2015-12-31"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data)

    def test_date_range_only_from(self, config: TestConfig):
        """Test date range with only From (>=)"""
        params = {"ncbiReleaseDateFrom": "2020-01-01"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        assert compare_counts(lapis_data, querulus_data)

    def test_range_with_details_endpoint(self, config: TestConfig):
        """Test that range queries work with details endpoint"""
        params = {"lengthFrom": "10000", "lengthTo": "11000", "limit": "10"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        # Both should return same number of results
        assert len(lapis_data) == len(querulus_data)

    def test_range_with_grouping(self, config: TestConfig):
        """Test that range queries work with grouping"""
        params = {"lengthFrom": "10000", "lengthTo": "11000", "fields": "geoLocCountry"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()["data"]
        querulus_data = querulus_resp.json()["data"]

        # Check that grouped counts match
        assert compare_counts(lapis_data, querulus_data)


class TestDataFormats:
    """Test different data format options (JSON, FASTA, TSV)"""

    def test_nucleotide_sequences_json_format(self, config: TestConfig):
        """Test JSON format for nucleotide sequences"""
        params = {"limit": "2", "dataFormat": "JSON"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/alignedNucleotideSequences"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/alignedNucleotideSequences"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()
        querulus_data = querulus_resp.json()

        # Both should be arrays
        assert isinstance(lapis_data, list)
        assert isinstance(querulus_data, list)
        assert len(querulus_data) == len(lapis_data)

        # Check structure of first item
        assert "accessionVersion" in querulus_data[0]
        assert "main" in querulus_data[0]
        assert querulus_data[0]["accessionVersion"] == lapis_data[0]["accessionVersion"]

    def test_nucleotide_sequences_fasta_format_default(self, config: TestConfig):
        """Test FASTA format for nucleotide sequences (default)"""
        params = {"limit": "2"}

        resp = requests.get(config.querulus_endpoint("sample/alignedNucleotideSequences"), params=params)
        assert resp.status_code == 200
        assert "text/x-fasta" in resp.headers["content-type"]

        # Check FASTA format
        content = resp.text
        assert content.startswith(">")
        assert "\n" in content

    def test_amino_acid_sequences_json_format(self, config: TestConfig):
        """Test JSON format for amino acid sequences"""
        params = {"limit": "10", "dataFormat": "JSON"}

        # Use a known gene for west-nile
        lapis_resp = requests.get(config.lapis_endpoint("sample/alignedAminoAcidSequences/2K"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/alignedAminoAcidSequences/2K"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200

        lapis_data = lapis_resp.json()
        querulus_data = querulus_resp.json()

        # Both should be arrays
        assert isinstance(lapis_data, list)
        assert isinstance(querulus_data, list)

        # Check structure (if we have data)
        if len(querulus_data) > 0:
            assert "accessionVersion" in querulus_data[0]
            assert "2K" in querulus_data[0]  # Gene name as key

    def test_aggregated_tsv_format(self, config: TestConfig):
        """Test TSV format for aggregated data"""
        params = {"fields": "geoLocCountry", "dataFormat": "tsv"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/aggregated"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200
        assert "text/tab-separated-values" in querulus_resp.headers["content-type"]

        # Parse TSV
        querulus_lines = querulus_resp.text.strip().split("\n")
        lapis_lines = lapis_resp.text.strip().split("\n")

        # Check header
        assert querulus_lines[0].split("\t") == ["geoLocCountry", "count"]

        # Check that we have data rows
        assert len(querulus_lines) > 1

        # Verify TSV format (tabs separate columns)
        for line in querulus_lines[1:5]:  # Check first few data rows
            parts = line.split("\t")
            assert len(parts) == 2  # Should have 2 columns

    def test_details_tsv_format(self, config: TestConfig):
        """Test TSV format for details data"""
        params = {"limit": "5", "dataFormat": "tsv", "fields": "accession,version,geoLocCountry"}

        lapis_resp = requests.get(config.lapis_endpoint("sample/details"), params=params)
        querulus_resp = requests.get(config.querulus_endpoint("sample/details"), params=params)

        assert lapis_resp.status_code == 200
        assert querulus_resp.status_code == 200
        assert "text/tab-separated-values" in querulus_resp.headers["content-type"]

        # Parse TSV
        querulus_lines = querulus_resp.text.strip().split("\n")

        # Check header exists
        assert len(querulus_lines) > 0
        headers = querulus_lines[0].split("\t")
        assert "accession" in headers
        assert "version" in headers

        # Check data rows
        assert len(querulus_lines) == 6  # header + 5 data rows

    def test_aggregated_json_format_default(self, config: TestConfig):
        """Test that JSON is the default format for aggregated"""
        params = {"fields": "geoLocCountry", "limit": "3"}

        resp = requests.get(config.querulus_endpoint("sample/aggregated"), params=params)
        assert resp.status_code == 200

        # Should return JSON by default
        data = resp.json()
        assert "data" in data
        assert "info" in data


if __name__ == "__main__":
    # Run tests with pytest
    import sys
    pytest.main([__file__, "-v"] + sys.argv[1:])
