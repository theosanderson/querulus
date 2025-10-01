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

        # Build SELECT clause
        if selected_fields:
            # Use quoted identifiers to preserve camelCase
            select_parts = []
            for field in selected_fields:
                # Check if it's a direct column (accession, version) or metadata field
                if field in ["accession", "version"]:
                    select_parts.append(field)
                else:
                    select_parts.append(
                        f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                    )
            select_clause = ", ".join(select_parts)
        else:
            # Return all metadata - expand JSONB into individual columns
            # For now, return the whole JSONB (we can expand this later)
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
