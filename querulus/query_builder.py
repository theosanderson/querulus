"""Query builder for translating LAPIS parameters to SQL"""

from typing import Any
from sqlalchemy import text
from sqlalchemy.sql import Select


class QueryBuilder:
    """Builds SQL queries from LAPIS-style parameters"""

    def __init__(self, organism: str, organism_config: Any = None):
        self.organism = organism
        self.organism_config = organism_config
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

    def build_earliest_release_date_expression(self) -> str:
        """
        Build SQL expression for earliestReleaseDate.

        Returns the LEAST of:
        - released_at
        - any configured external date fields
        - minimum earliestReleaseDate from previous versions
        """
        if not self.organism_config:
            # Fallback to just released_at if no config
            return "released_at"

        earliest_config = self.organism_config.schema.get('earliestReleaseDate', {})
        if not earliest_config.get('enabled', False):
            return "released_at"

        external_fields = earliest_config.get('externalFields', [])

        # Build LEAST expression with released_at and external fields
        parts = ["released_at"]
        for field in external_fields:
            # Convert string date to timestamp, handle NULL
            parts.append(
                f"(joint_metadata -> 'metadata' ->> '{field}')::timestamp"
            )

        # Also need to consider earliestReleaseDate from earlier versions
        # Use window function to get the minimum across all versions of this accession
        least_expr = "LEAST(" + ", ".join(parts) + ")"

        # Apply window function to get minimum across versions
        # This ensures later versions inherit earlier earliestReleaseDate
        return f"LEAST({least_expr}, MIN({least_expr}) OVER (PARTITION BY accession ORDER BY version ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))"

    def build_data_use_terms_expression(self) -> str:
        """
        Build SQL expression for dataUseTerms field.
        Returns 'OPEN' if not restricted or if restriction has expired, else 'RESTRICTED'.
        """
        if not self.organism_config:
            return "'OPEN'"

        # Check if dataUseTerms are enabled in config
        data_use_terms_config = self.organism_config.backend_config.get('dataUseTerms', {})
        if not data_use_terms_config.get('enabled', False):
            return "'OPEN'"

        return """
            CASE
                WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'
                     AND data_use_terms_table.restricted_until > NOW()
                THEN 'RESTRICTED'
                ELSE 'OPEN'
            END
        """

    def build_data_use_terms_restricted_until_expression(self) -> str:
        """
        Build SQL expression for dataUseTermsRestrictedUntil field.
        Returns the restriction date if currently restricted, else NULL.
        """
        if not self.organism_config:
            return "NULL"

        data_use_terms_config = self.organism_config.backend_config.get('dataUseTerms', {})
        if not data_use_terms_config.get('enabled', False):
            return "NULL"

        return """
            CASE
                WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'
                     AND data_use_terms_table.restricted_until > NOW()
                THEN TO_CHAR(data_use_terms_table.restricted_until, 'YYYY-MM-DD')
                ELSE NULL
            END
        """

    def build_data_use_terms_url_expression(self) -> str:
        """
        Build SQL expression for dataUseTermsUrl field.
        Returns the appropriate URL based on data use terms status.
        """
        if not self.organism_config:
            return "NULL"

        data_use_terms_config = self.organism_config.backend_config.get('dataUseTerms', {})
        if not data_use_terms_config.get('enabled', False):
            return "NULL"

        urls = data_use_terms_config.get('urls', {})
        open_url = urls.get('open', '')
        restricted_url = urls.get('restricted', '')

        return f"""
            CASE
                WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'
                     AND data_use_terms_table.restricted_until > NOW()
                THEN '{restricted_url}'
                ELSE '{open_url}'
            END
        """

    def build_aggregated_query(
        self, limit: int | None = None, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        """
        Build aggregated count query with optional grouping.

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
            "earliestReleaseDate",
            "submissionId",
            "submitter",
            "groupId",
            "groupName",
            "isRevocation",
            "versionComment",
            "dataUseTerms",
            "dataUseTermsRestrictedUntil",
            "dataUseTermsUrl",
        }

        # Base selection
        if self.group_by_fields:
            # Check if versionStatus or earliestReleaseDate is one of the grouping fields OR filters
            # Both require window functions, so we need CTE approach
            needs_version_status = "versionStatus" in self.group_by_fields or "versionStatus" in self.filters
            needs_earliest_release = "earliestReleaseDate" in self.group_by_fields or "earliestReleaseDate" in self.filters
            needs_cte = needs_version_status or needs_earliest_release

            if needs_cte:
                # Need to use a CTE because window functions can't be in GROUP BY
                # First compute versionStatus for each row, then aggregate
                # Include both group_by_fields and any filtered computed fields
                all_fields = set(self.group_by_fields)
                all_fields.update(f for f in self.filters.keys() if f in computed_fields)

                cte_select_parts = []
                for field in all_fields:
                    if field == "versionStatus":
                        cte_select_parts.append(f"""
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
                    elif field == "earliestReleaseDate":
                        earliest_expr = self.build_earliest_release_date_expression()
                        cte_select_parts.append(
                            f"TO_CHAR({earliest_expr}, 'YYYY-MM-DD') AS \"{field}\""
                        )
                    else:
                        cte_select_parts.append(
                            f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                        )

                cte_select = ", ".join(cte_select_parts)

                # Now build outer query that groups by the computed fields
                outer_select_parts = [f'"{field}"' for field in self.group_by_fields]
                outer_select_parts.append("COUNT(*) as count")
                select_clause = ", ".join(outer_select_parts)

                group_by_parts = [f'"{field}"' for field in self.group_by_fields]
                group_by_clause = ", ".join(group_by_parts)

                # Build CTE query
                query = f"""
                    WITH computed_fields AS (
                        SELECT {cte_select}
                        FROM sequence_entries_view
                        WHERE organism = :organism
                          AND released_at IS NOT NULL
                """
                # Add non-computed field filters in the CTE
                if self.filters:
                    for field, value in self.filters.items():
                        if field not in computed_fields:
                            param_name = f"filter_{field}"
                            query += f"\n          AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                            params[param_name] = value

                query += f"""
                    )
                    SELECT {select_clause}
                    FROM computed_fields
                    WHERE 1=1
                """

                # Add computed field filters in outer WHERE clause
                if self.filters:
                    for field, value in self.filters.items():
                        if field in computed_fields:
                            param_name = f"filter_{field}"
                            query += f'\n  AND "{field}" = :{param_name}'
                            params[param_name] = value

                # Set group_by_clause for later use
                # We'll add GROUP BY after the query is built
                query_needs_groupby = True
                filters_already_applied = True
            else:
                # Build SELECT with grouped fields (no versionStatus)
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
                query_needs_groupby = True
                filters_already_applied = False
        else:
            # Simple count (no grouping)
            # Check if we need CTE for computed field filters
            needs_version_status_filter = "versionStatus" in self.filters
            needs_earliest_release_filter = "earliestReleaseDate" in self.filters
            needs_cte_for_filter = needs_version_status_filter or needs_earliest_release_filter

            if needs_cte_for_filter:
                # Build CTE to compute the filtered fields
                cte_select_parts = []
                for field in self.filters.keys():
                    if field == "versionStatus":
                        cte_select_parts.append(f"""
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
                    elif field == "earliestReleaseDate":
                        earliest_expr = self.build_earliest_release_date_expression()
                        cte_select_parts.append(
                            f"TO_CHAR({earliest_expr}, 'YYYY-MM-DD') AS \"{field}\""
                        )

                cte_select = ", ".join(cte_select_parts)

                query = f"""
                    WITH computed_fields AS (
                        SELECT {cte_select}
                        FROM sequence_entries_view
                        WHERE organism = :organism
                          AND released_at IS NOT NULL
                """

                # Add non-computed field filters in CTE
                if self.filters:
                    for field, value in self.filters.items():
                        if field not in computed_fields:
                            param_name = f"filter_{field}"
                            query += f"\n          AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                            params[param_name] = value

                query += """
                    )
                    SELECT COUNT(*) as count
                    FROM computed_fields
                    WHERE 1=1
                """

                # Add computed field filters in outer query
                if self.filters:
                    for field, value in self.filters.items():
                        if field in computed_fields:
                            param_name = f"filter_{field}"
                            query += f'\n  AND "{field}" = :{param_name}'
                            params[param_name] = value

                filters_already_applied = True
                group_by_clause = None
                query_needs_groupby = False
            else:
                # Simple count without computed fields
                query = """
                    SELECT COUNT(*) as count
                    FROM sequence_entries_view
                    WHERE organism = :organism
                      AND released_at IS NOT NULL
                """
                group_by_clause = None
                query_needs_groupby = False
                filters_already_applied = False

        # Add metadata filters (if not already applied in CTE)
        if self.filters and not filters_already_applied:
            for field, value in self.filters.items():
                param_name = f"filter_{field}"
                query += f"\n  AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                params[param_name] = value

        # Add GROUP BY if needed
        if query_needs_groupby:
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
            "earliestReleaseDate",
            "submissionId",
            "submitter",
            "groupId",
            "groupName",
            "isRevocation",
            "versionComment",
            "dataUseTerms",
            "dataUseTermsRestrictedUntil",
            "dataUseTermsUrl",
        }

        # Check if versionStatus or earliestReleaseDate is requested or filtered
        needs_version_status = (selected_fields and "versionStatus" in selected_fields) or "versionStatus" in self.filters
        needs_earliest_release = (selected_fields and "earliestReleaseDate" in selected_fields) or "earliestReleaseDate" in self.filters

        # If we need versionStatus or earliestReleaseDate (for selection or filtering), use CTE
        needs_cte = needs_version_status or needs_earliest_release

        if needs_cte:
            # Determine all fields we need to compute (selected fields + filtered fields)
            all_fields = set(selected_fields) if selected_fields else set()
            all_fields.update(self.filters.keys())
            # Always include accession for ordering
            all_fields.add("accession")

            # Build SELECT clause for CTE
            select_parts = []
            for field in all_fields:
                if field == "accession":
                    select_parts.append("sequence_entries_view.accession AS accession")
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
                elif field == "earliestReleaseDate":
                    earliest_expr = self.build_earliest_release_date_expression()
                    select_parts.append(
                        f"TO_CHAR({earliest_expr}, 'YYYY-MM-DD') AS \"{field}\""
                    )
                elif field == "submissionId":
                    select_parts.append(f'submission_id AS "{field}"')
                elif field == "submitter":
                    select_parts.append(f'submitter AS "{field}"')
                elif field == "groupId":
                    select_parts.append(f'sequence_entries_view.group_id AS "{field}"')
                elif field == "isRevocation":
                    select_parts.append(f'is_revocation AS "{field}"')
                elif field == "versionComment":
                    select_parts.append(f'version_comment AS "{field}"')
                elif field == "groupName":
                    select_parts.append(f'groups_table.group_name AS "{field}"')
                elif field == "dataUseTerms":
                    data_use_expr = self.build_data_use_terms_expression()
                    select_parts.append(f'({data_use_expr}) AS "{field}"')
                elif field == "dataUseTermsRestrictedUntil":
                    restricted_until_expr = self.build_data_use_terms_restricted_until_expression()
                    select_parts.append(f'({restricted_until_expr}) AS "{field}"')
                elif field == "dataUseTermsUrl":
                    url_expr = self.build_data_use_terms_url_expression()
                    select_parts.append(f'({url_expr}) AS "{field}"')
                else:
                    # Metadata field from JSONB
                    select_parts.append(
                        f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                    )
            cte_select_clause = ", ".join(select_parts)

            # Determine which fields to select in outer query
            if selected_fields:
                outer_select_parts = [f'"{field}"' for field in selected_fields]
                outer_select_clause = ", ".join(outer_select_parts)
            else:
                # Select all computed fields
                outer_select_clause = "*"

            # Check if we need JOINs in the CTE
            needs_groups_join = "groupName" in all_fields
            needs_data_use_terms_join = any(
                f in all_fields for f in ["dataUseTerms", "dataUseTermsRestrictedUntil", "dataUseTermsUrl"]
            )

            # Build FROM clause with necessary JOINs
            from_clause = "FROM sequence_entries_view"
            if needs_groups_join:
                from_clause += "\n                    LEFT JOIN groups_table ON sequence_entries_view.group_id = groups_table.group_id"
            if needs_data_use_terms_join:
                from_clause += "\n                    LEFT JOIN data_use_terms_table ON sequence_entries_view.accession = data_use_terms_table.accession"

            # Build the CTE query
            query = f"""
                WITH computed_fields AS (
                    SELECT {cte_select_clause}
                    {from_clause}
                    WHERE organism = :organism
                      AND released_at IS NOT NULL
            """

            # Add metadata filters (only non-computed fields in CTE WHERE clause)
            if self.filters:
                for field, value in self.filters.items():
                    if field not in computed_fields:
                        # Regular metadata field - filter in CTE
                        param_name = f"filter_{field}"
                        query += f"\n          AND joint_metadata -> 'metadata' ->> '{field}' = :{param_name}"
                        params[param_name] = value

            # Close the CTE and select from it
            query += f"""
                )
                SELECT {outer_select_clause}
                FROM computed_fields
                WHERE 1=1
            """

            # Add computed field filters in outer WHERE clause
            if self.filters:
                for field, value in self.filters.items():
                    if field in computed_fields:
                        # Computed field - filter in outer query
                        param_name = f"filter_{field}"
                        query += f'\n  AND "{field}" = :{param_name}'
                        params[param_name] = value

            # Add pagination
            query += "\nORDER BY accession"
            if limit is not None:
                query += f"\nLIMIT {limit}"
            if offset > 0:
                query += f"\nOFFSET {offset}"

            return query, params

        else:
            # Simpler query without computed fields that need CTE
            if selected_fields:
                select_parts = []
                for field in selected_fields:
                    if field == "accession":
                        select_parts.append("sequence_entries_view.accession AS accession")
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
                    elif field == "earliestReleaseDate":
                        earliest_expr = self.build_earliest_release_date_expression()
                        select_parts.append(
                            f"TO_CHAR({earliest_expr}, 'YYYY-MM-DD') AS \"{field}\""
                        )
                    elif field == "submissionId":
                        select_parts.append(f'submission_id AS "{field}"')
                    elif field == "submitter":
                        select_parts.append(f'submitter AS "{field}"')
                    elif field == "groupId":
                        select_parts.append(f'sequence_entries_view.group_id AS "{field}"')
                    elif field == "isRevocation":
                        select_parts.append(f'is_revocation AS "{field}"')
                    elif field == "versionComment":
                        select_parts.append(f'version_comment AS "{field}"')
                    elif field == "groupName":
                        select_parts.append(f'groups_table.group_name AS "{field}"')
                    elif field == "dataUseTerms":
                        data_use_expr = self.build_data_use_terms_expression()
                        select_parts.append(f'({data_use_expr}) AS "{field}"')
                    elif field == "dataUseTermsRestrictedUntil":
                        restricted_until_expr = self.build_data_use_terms_restricted_until_expression()
                        select_parts.append(f'({restricted_until_expr}) AS "{field}"')
                    elif field == "dataUseTermsUrl":
                        url_expr = self.build_data_use_terms_url_expression()
                        select_parts.append(f'({url_expr}) AS "{field}"')
                    else:
                        # Metadata field from JSONB
                        select_parts.append(
                            f'joint_metadata -> \'metadata\' ->> \'{field}\' AS "{field}"'
                        )
                select_clause = ", ".join(select_parts)
            else:
                # Return all metadata
                select_clause = "accession, version, joint_metadata -> 'metadata' AS metadata"

        # Check if we need JOINs
        needs_groups_join = selected_fields and "groupName" in selected_fields
        needs_data_use_terms_join = selected_fields and any(
            f in selected_fields for f in ["dataUseTerms", "dataUseTermsRestrictedUntil", "dataUseTermsUrl"]
        )

        # Build FROM clause with necessary JOINs
        from_clause = "FROM sequence_entries_view"
        if needs_groups_join:
            from_clause += "\n                LEFT JOIN groups_table ON sequence_entries_view.group_id = groups_table.group_id"
        if needs_data_use_terms_join:
            from_clause += "\n                LEFT JOIN data_use_terms_table ON sequence_entries_view.accession = data_use_terms_table.accession"

        query = f"""
            SELECT {select_clause}
            {from_clause}
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
        query += "\nORDER BY sequence_entries_view.accession"
        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset > 0:
            query += f"\nOFFSET {offset}"

        return query, params
