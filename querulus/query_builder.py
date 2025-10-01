"""Query builder for translating LAPIS parameters to SQL"""

from typing import Any
from sqlalchemy import text
from sqlalchemy.sql import Select


class QueryBuilder:
    """Builds SQL queries from LAPIS-style parameters"""

    def __init__(self, organism: str):
        self.organism = organism
        self.filters: dict[str, Any] = {}
        self.group_by_fields: list[str] = []

    def add_filter(self, field: str, value: Any) -> "QueryBuilder":
        """Add a metadata filter"""
        self.filters[field] = value
        return self

    def add_filters_from_params(self, params: dict[str, Any]) -> "QueryBuilder":
        """
        Add filters from query parameters.

        Filters are any parameters that aren't special LAPIS parameters like
        'fields', 'orderBy', 'limit', 'offset', etc.
        """
        special_params = {"fields", "orderBy", "limit", "offset", "format", "downloadAsFile"}
        for key, value in params.items():
            if key not in special_params and value is not None:
                self.filters[key] = value
        return self

    def set_group_by_fields(self, fields: list[str]) -> "QueryBuilder":
        """Set fields to group by"""
        self.group_by_fields = fields
        return self

    def build_aggregated_query(
        self, limit: int | None = None, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """
        Build aggregated count query with optional grouping.

        Returns: (query_string, bind_params)
        """
        params = {"organism": self.organism}

        # Base selection
        if self.group_by_fields:
            # Build SELECT with grouped fields
            # Use quoted identifiers to preserve camelCase column names
            select_parts = []
            for field in self.group_by_fields:
                select_parts.append(
                    f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                )
            select_parts.append("COUNT(*) as count")
            select_clause = ", ".join(select_parts)

            # Build GROUP BY
            group_by_parts = []
            for field in self.group_by_fields:
                group_by_parts.append(f"joint_metadata -> 'metadata' ->> '{field}'")
            group_by_clause = ", ".join(group_by_parts)

            query = f"""
                SELECT {select_clause}
                FROM sequence_entries_view
                WHERE organism = :organism
                  AND released_at IS NOT NULL
            """
        else:
            # Simple count
            query = """
                SELECT COUNT(*) as count
                FROM sequence_entries_view
                WHERE organism = :organism
                  AND released_at IS NOT NULL
            """
            group_by_clause = None

        # Add metadata filters
        if self.filters:
            for field, value in self.filters.items():
                param_name = f"filter_{field}"
                query += f"\n  AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                params[param_name] = value

        # Add GROUP BY if needed
        if self.group_by_fields:
            query += f"\nGROUP BY {group_by_clause}"
            query += "\nORDER BY count DESC"

            # Add pagination for grouped results
            if limit is not None:
                query += f"\nLIMIT {limit}"
            if offset > 0:
                query += f"\nOFFSET {offset}"

        return query, params

    def build_details_query(
        self, selected_fields: list[str] | None = None, limit: int | None = None, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """
        Build details query to retrieve metadata.

        Returns: (query_string, bind_params)
        """
        params = {"organism": self.organism}

        # Computed fields that need special handling
        computed_fields = {
            "accessionVersion",
            "displayName",
            "versionStatus",
            "submittedDate",
            "submittedAtTimestamp",
            "releasedDate",
            "releasedAtTimestamp",
        }

        # Check if versionStatus is requested
        needs_version_status = selected_fields and "versionStatus" in selected_fields

        # If we need versionStatus, wrap the query in a CTE to compute it
        if needs_version_status:
            # Build SELECT clause for CTE
            select_parts = []
            for field in selected_fields:
                if field == "accession":
                    select_parts.append("accession")
                elif field == "version":
                    select_parts.append("version")
                elif field == "accessionVersion" or field == "displayName":
                    select_parts.append(f"(accession || '.' || version) AS \"{field}\"")
                elif field == "submittedDate":
                    select_parts.append(f"TO_CHAR(submitted_at, 'YYYY-MM-DD') AS \"{field}\"")
                elif field == "submittedAtTimestamp":
                    select_parts.append(
                        f"EXTRACT(EPOCH FROM submitted_at)::bigint AS \"{field}\""
                    )
                elif field == "releasedDate":
                    select_parts.append(f"TO_CHAR(released_at, 'YYYY-MM-DD') AS \"{field}\"")
                elif field == "releasedAtTimestamp":
                    select_parts.append(
                        f"EXTRACT(EPOCH FROM released_at)::bigint AS \"{field}\""
                    )
                elif field == "versionStatus":
                    # Compute versionStatus using window functions
                    select_parts.append(f"""
                        CASE
                            WHEN version = MAX(version) OVER (PARTITION BY accession) THEN 'LATEST_VERSION'
                            WHEN EXISTS (
                                SELECT 1 FROM sequence_entries_view sev2
                                WHERE sev2.accession = sequence_entries_view.accession
                                  AND sev2.version > sequence_entries_view.version
                                  AND sev2.is_revocation = true
                                  AND sev2.organism = :organism
                                  AND sev2.released_at IS NOT NULL
                            ) THEN 'REVOKED'
                            ELSE 'REVISED'
                        END AS "{field}"
                    """)
                else:
                    # Metadata field from JSONB
                    select_parts.append(
                        f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                    )
            select_clause = ", ".join(select_parts)
        else:
            # Simpler query without versionStatus
            if selected_fields:
                select_parts = []
                for field in selected_fields:
                    if field == "accession":
                        select_parts.append("accession")
                    elif field == "version":
                        select_parts.append("version")
                    elif field == "accessionVersion" or field == "displayName":
                        select_parts.append(f"(accession || '.' || version) AS \"{field}\"")
                    elif field == "submittedDate":
                        select_parts.append(f"TO_CHAR(submitted_at, 'YYYY-MM-DD') AS \"{field}\"")
                    elif field == "submittedAtTimestamp":
                        select_parts.append(
                            f"EXTRACT(EPOCH FROM submitted_at)::bigint AS \"{field}\""
                        )
                    elif field == "releasedDate":
                        select_parts.append(f"TO_CHAR(released_at, 'YYYY-MM-DD') AS \"{field}\"")
                    elif field == "releasedAtTimestamp":
                        select_parts.append(
                            f"EXTRACT(EPOCH FROM released_at)::bigint AS \"{field}\""
                        )
                    else:
                        # Metadata field from JSONB
                        select_parts.append(
                            f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                        )
                select_clause = ", ".join(select_parts)
            else:
                # Return all metadata
                select_clause = "accession, version, joint_metadata -> 'metadata' AS metadata"

        query = f"""
            SELECT {select_clause}
            FROM sequence_entries_view
            WHERE organism = :organism
              AND released_at IS NOT NULL
        """

        # Add metadata filters
        if self.filters:
            for field, value in self.filters.items():
                param_name = f"filter_{field}"
                query += f"\n  AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                params[param_name] = value

        # Add pagination
        query += "\nORDER BY accession"
        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset > 0:
            query += f"\nOFFSET {offset}"

        return query, params
