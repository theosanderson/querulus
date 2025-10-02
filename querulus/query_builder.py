"""Query builder for translating LAPIS parameters to SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence


BASE_TABLE = "sequence_entries_view"
JOIN_GROUPS = "groups"
JOIN_DATA_USE_TERMS = "data_use_terms"

JOIN_SQL: dict[str, str] = {
    JOIN_GROUPS: "LEFT JOIN groups_table ON sequence_entries_view.group_id = groups_table.group_id",
    JOIN_DATA_USE_TERMS: "LEFT JOIN data_use_terms_table ON sequence_entries_view.accession = data_use_terms_table.accession",
}

_PARAM_SANITIZER = re.compile(r"[^a-zA-Z0-9_]")


ExpressionFactory = Callable[["QueryBuilder"], str]
OrderFactory = Callable[["QueryBuilder"], Sequence[str]]


@dataclass(frozen=True)
class FieldDefinition:
    """Configuration describing how to project and filter a logical field."""

    name: str
    expression_factory: ExpressionFactory
    requires_cte: bool = False
    joins: tuple[str, ...] = ()
    order_base: Sequence[str] | OrderFactory | None = None
    order_alias: Sequence[str] | OrderFactory | None = None
    filter_factory: ExpressionFactory | None = None
    group_factory: ExpressionFactory | None = None
    order_dependencies: tuple[str, ...] = ()

    def expression(self, builder: "QueryBuilder") -> str:
        return self.expression_factory(builder)

    def select_sql(self, builder: "QueryBuilder") -> str:
        return f"{self.expression(builder)} AS \"{self.name}\""

    def filter_sql(self, builder: "QueryBuilder") -> str:
        factory = self.filter_factory or self.expression_factory
        return factory(builder)

    def group_sql(self, builder: "QueryBuilder") -> str:
        factory = self.group_factory or self.expression_factory
        return factory(builder)

    def order_sql(self, builder: "QueryBuilder", *, use_alias: bool) -> Sequence[str]:
        raw: Sequence[str] | OrderFactory | None
        raw = self.order_alias if use_alias else self.order_base

        if raw is None:
            return (f"\"{self.name}\"" if use_alias else self.expression(builder),)

        if callable(raw):
            raw = raw(builder)

        return tuple(raw)


_METADATA_FIELD_CACHE: dict[str, FieldDefinition] = {}


def _metadata_field_definition(field: str) -> FieldDefinition:
    """Create and cache a definition for a metadata JSON field."""
    cached = _METADATA_FIELD_CACHE.get(field)
    if cached:
        return cached

    json_key = field.replace("'", "''")

    def metadata_expr(_: "QueryBuilder", key: str = json_key) -> str:
        return f"joint_metadata -> 'metadata' ->> '{key}'"

    definition = FieldDefinition(name=field, expression_factory=metadata_expr)
    _METADATA_FIELD_CACHE[field] = definition
    return definition


class QueryBuilder:
    """Builds SQL queries from LAPIS-style parameters."""

    def __init__(self, organism: str, organism_config: Any = None):
        self.organism = organism
        self.organism_config = organism_config
        self.filters: dict[str, Any] = {}
        self.group_by_fields: list[str] = []
        self.order_by_fields: list[str] = []

    # ------------------------------------------------------------------
    # Public API for configuring the builder
    # ------------------------------------------------------------------
    def add_filter(self, field: str, value: Any) -> "QueryBuilder":
        self.filters[field] = value
        return self

    def add_filters_from_params(self, params: dict[str, Any]) -> "QueryBuilder":
        special_params = {
            "fields", "orderBy", "limit", "offset", "format",
            "downloadAsFile", "downloadFileBasename", "dataFormat",
            "dataUseTerms", "dataUseTermsRestrictedUntil", "versionStatus"
        }
        for key, value in params.items():
            if key not in special_params and value is not None:
                if key == "isRevocation" and isinstance(value, str):
                    value = value.lower() == "true"
                self.filters[key] = value
        return self

    def set_group_by_fields(self, fields: list[str]) -> "QueryBuilder":
        self.group_by_fields = fields
        return self

    def set_order_by_fields(self, fields: list[str]) -> "QueryBuilder":
        self.order_by_fields = fields
        return self

    # ------------------------------------------------------------------
    # Core helpers
    # ------------------------------------------------------------------
    def _field_definition(self, field: str) -> FieldDefinition:
        definition = FIELD_DEFINITIONS.get(field)
        if definition:
            return definition
        return _metadata_field_definition(field)

    def _resolve_filter_base(self, field: str) -> tuple[str, str | None]:
        if field.endswith("From"):
            return field[:-4], ">="
        if field.endswith("To"):
            return field[:-2], "<="
        return field, None

    def _filter_base_fields(self) -> list[str]:
        base_fields = []
        for field in self.filters:
            base_field, _ = self._resolve_filter_base(field)
            base_fields.append(base_field)
        return base_fields

    def _order_dependency_fields(self) -> list[str]:
        dependencies: list[str] = []
        for field in self.order_by_fields:
            if field in {"random", "count"}:
                continue
            definition = self._field_definition(field)
            dependencies.append(field)
            dependencies.extend(list(definition.order_dependencies))
        return dependencies

    def _requires_cte(self, fields: Iterable[str]) -> bool:
        return any(self._field_definition(field).requires_cte for field in fields)

    def _collect_join_requirements(self, fields: Iterable[str]) -> set[str]:
        joins: set[str] = set()
        for field in fields:
            joins.update(self._field_definition(field).joins)
        return joins

    def _build_join_sql(self, joins: Iterable[str], *, indent: str) -> str:
        clauses = []
        join_order = [JOIN_GROUPS, JOIN_DATA_USE_TERMS]
        join_set = {j for j in joins if j in JOIN_SQL}
        for name in join_order:
            if name in join_set:
                clauses.append(f"{indent}{JOIN_SQL[name]}")
        if not clauses:
            return ""
        return "\n" + "\n".join(clauses)

    @staticmethod
    def _ordered_unique(items: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                ordered.append(item)
        return ordered

    def _append_filter_clause(
        self,
        clauses: list[str],
        *,
        field: str,
        value: Any,
        params: dict[str, Any],
        use_alias: bool,
    ) -> None:
        base_field, operator = self._resolve_filter_base(field)
        definition = self._field_definition(base_field)
        expression = f'"{base_field}"' if use_alias else definition.filter_sql(self)

        param_prefix = f"filter_{_PARAM_SANITIZER.sub('_', field)}"
        clause = self._render_filter_condition(
            expression,
            value,
            operator,
            params,
            param_prefix,
        )
        clauses.append(clause)

    def _render_filter_condition(
        self,
        expression: str,
        value: Any,
        operator: str | None,
        params: dict[str, Any],
        param_prefix: str,
    ) -> str:
        if isinstance(value, list):
            placeholders = []
            for idx, item in enumerate(value):
                param_name = f"{param_prefix}_{idx}"
                params[param_name] = item
                placeholders.append(f":{param_name}")
            return f"{expression} IN ({', '.join(placeholders)})"

        param_name = param_prefix
        params[param_name] = value
        op = operator or "="
        return f"{expression} {op} :{param_name}"

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------
    def build_order_by_clause(self, context: str = "details") -> str:
        if not self.order_by_fields:
            return "count DESC" if context == "aggregated" else '"accession"'

        order_fragments: list[str] = []
        for field in self.order_by_fields:
            if field == "random":
                order_fragments.append("RANDOM()")
                continue
            if field == "count" and context == "aggregated":
                order_fragments.append("count")
                continue

            definition = self._field_definition(field)
            fragments = definition.order_sql(self, use_alias=True)
            order_fragments.extend(fragments)

        return ", ".join(order_fragments)

    # ------------------------------------------------------------------
    # Aggregated queries
    # ------------------------------------------------------------------
    def build_aggregated_query(
        self, limit: int | None = None, offset: int = 0
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"organism": self.organism}

        filter_base_fields = self._filter_base_fields()
        order_dependency_fields = self._order_dependency_fields()

        cte_candidate_fields = set(self.group_by_fields)
        cte_candidate_fields.update(filter_base_fields)
        cte_candidate_fields.update(order_dependency_fields)

        if self.group_by_fields:
            if self._requires_cte(cte_candidate_fields):
                query = self._build_aggregated_query_with_cte(
                    params,
                    filter_base_fields,
                    limit,
                    offset,
                )
            else:
                query = self._build_aggregated_query_simple(
                    params,
                    filter_base_fields,
                    limit,
                    offset,
                )
        else:
            if self._requires_cte(cte_candidate_fields):
                query = self._build_aggregated_count_with_cte(params, filter_base_fields)
            else:
                query = self._build_aggregated_count(params, filter_base_fields)

        return query, params

    def _build_aggregated_query_simple(
        self,
        params: dict[str, Any],
        filter_base_fields: list[str],
        limit: int | None,
        offset: int,
    ) -> str:
        fields = self.group_by_fields
        select_parts = [self._field_definition(field).select_sql(self) for field in fields]
        select_parts.append("COUNT(*) AS count")
        select_clause = ",\n        ".join(select_parts)

        join_fields = set(fields)
        join_fields.update(filter_base_fields)
        join_fields.update(self._order_dependency_fields())

        query = (
            "SELECT\n"
            f"        {select_clause}\n"
            f"FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(join_fields, indent="    ")
        query += (
            "\nWHERE organism = :organism"
            "\n  AND released_at IS NOT NULL"
        )

        where_clauses: list[str] = []
        for field, value in self.filters.items():
            self._append_filter_clause(where_clauses, field=field, value=value, params=params, use_alias=False)

        for clause in where_clauses:
            query += f"\n  AND {clause}"

        group_by = ", ".join(self._field_definition(field).group_sql(self) for field in fields)
        query += f"\nGROUP BY {group_by}"

        order_clause = self.build_order_by_clause("aggregated")
        query += f"\nORDER BY {order_clause}"

        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset > 0:
            query += f"\nOFFSET {offset}"

        return query

    def _build_aggregated_query_with_cte(
        self,
        params: dict[str, Any],
        filter_base_fields: list[str],
        limit: int | None,
        offset: int,
    ) -> str:
        group_fields = self.group_by_fields
        cte_fields = self._ordered_unique(
            list(group_fields)
            + filter_base_fields
            + self._order_dependency_fields()
        )

        joins = self._collect_join_requirements(cte_fields)

        select_parts = [self._field_definition(field).select_sql(self) for field in cte_fields]
        select_clause = ",\n        ".join(select_parts)

        cte_where: list[str] = []
        outer_where: list[str] = []
        for field, value in self.filters.items():
            base_field, _ = self._resolve_filter_base(field)
            if self._field_definition(base_field).requires_cte:
                self._append_filter_clause(
                    outer_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=True,
                )
            else:
                self._append_filter_clause(
                    cte_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=False,
                )

        query = (
            "WITH computed_fields AS (\n"
            "    SELECT\n"
            f"        {select_clause}\n"
            f"    FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(joins, indent="        ")
        query += (
            "\n    WHERE organism = :organism"
            "\n      AND released_at IS NOT NULL"
        )
        for clause in cte_where:
            query += f"\n      AND {clause}"
        query += "\n)\n"

        group_aliases = [f'"{field}"' for field in group_fields]
        outer_select_parts = group_aliases + ["COUNT(*) AS count"]
        query += "SELECT " + ", ".join(outer_select_parts) + "\nFROM computed_fields"

        if outer_where:
            query += "\nWHERE 1=1"
            for clause in outer_where:
                query += f"\n  AND {clause}"

        if group_aliases:
            query += "\nGROUP BY " + ", ".join(group_aliases)
            query += f"\nORDER BY {self.build_order_by_clause('aggregated')}"
            if limit is not None:
                query += f"\nLIMIT {limit}"
            if offset > 0:
                query += f"\nOFFSET {offset}"

        return query

    def _build_aggregated_count(
        self,
        params: dict[str, Any],
        filter_base_fields: list[str],
    ) -> str:
        joins = self._collect_join_requirements(filter_base_fields)

        query = f"SELECT COUNT(*) AS count\nFROM {BASE_TABLE}"
        query += self._build_join_sql(joins, indent="    ")
        query += (
            "\nWHERE organism = :organism"
            "\n  AND released_at IS NOT NULL"
        )

        for field, value in self.filters.items():
            clause_list: list[str] = []
            self._append_filter_clause(clause_list, field=field, value=value, params=params, use_alias=False)
            for clause in clause_list:
                query += f"\n  AND {clause}"

        return query

    def _build_aggregated_count_with_cte(
        self,
        params: dict[str, Any],
        filter_base_fields: list[str],
    ) -> str:
        cte_fields = self._ordered_unique(filter_base_fields or ["accession"])
        joins = self._collect_join_requirements(cte_fields)

        select_parts = [self._field_definition(field).select_sql(self) for field in cte_fields]
        select_clause = ",\n        ".join(select_parts)

        cte_where: list[str] = []
        outer_where: list[str] = []
        for field, value in self.filters.items():
            base_field, _ = self._resolve_filter_base(field)
            if self._field_definition(base_field).requires_cte:
                self._append_filter_clause(
                    outer_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=True,
                )
            else:
                self._append_filter_clause(
                    cte_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=False,
                )

        query = (
            "WITH computed_fields AS (\n"
            "    SELECT\n"
            f"        {select_clause}\n"
            f"    FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(joins, indent="        ")
        query += (
            "\n    WHERE organism = :organism"
            "\n      AND released_at IS NOT NULL"
        )
        for clause in cte_where:
            query += f"\n      AND {clause}"
        query += "\n)\n"

        query += "SELECT COUNT(*) AS count\nFROM computed_fields"
        if outer_where:
            query += "\nWHERE 1=1"
            for clause in outer_where:
                query += f"\n  AND {clause}"

        return query

    # ------------------------------------------------------------------
    # Sequence queries
    # ------------------------------------------------------------------
    def build_sequences_query(
        self,
        segment_name: str = "main",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[str, dict[str, Any]]:
        return self._build_sequence_query(
            segment_key="alignedNucleotideSequences",
            segment_name=segment_name,
            limit=limit,
            offset=offset,
        )

    def build_unaligned_sequences_query(
        self,
        segment_name: str = "main",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[str, dict[str, Any]]:
        return self._build_sequence_query(
            segment_key="unalignedNucleotideSequences",
            segment_name=segment_name,
            limit=limit,
            offset=offset,
        )

    def _build_sequence_query(
        self,
        *,
        segment_key: str,
        segment_name: str,
        limit: int | None,
        offset: int,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"organism": self.organism}

        json_path = (
            "joint_metadata -> '{key}' -> '{segment}' ->> 'compressedSequence'"
            .format(key=segment_key, segment=segment_name)
        )

        simple_filters: list[tuple[str, Any]] = []
        computed_filters: list[tuple[str, Any]] = []
        for field, value in self.filters.items():
            base_field, _ = self._resolve_filter_base(field)
            if self._field_definition(base_field).requires_cte:
                computed_filters.append((field, value))
            else:
                simple_filters.append((field, value))

        if not computed_filters:
            where_clauses: list[str] = []
            for field, value in simple_filters:
                self._append_filter_clause(where_clauses, field=field, value=value, params=params, use_alias=False)

            query = (
                "SELECT\n"
                "        accession,\n"
                "        version,\n"
                f"        {json_path} AS compressed_seq\n"
                f"FROM {BASE_TABLE}\n"
                "WHERE organism = :organism\n"
                "  AND released_at IS NOT NULL\n"
                f"  AND {json_path} IS NOT NULL"
            )

            for clause in where_clauses:
                query += f"\n  AND {clause}"

            query += "\nORDER BY \"accession\", \"version\""

            if limit is not None:
                query += f"\nLIMIT {limit}"
            if offset:
                query += f"\nOFFSET {offset}"

            return query, params

        # Build CTE to support computed-field filters
        computed_field_names = self._ordered_unique(
            [self._resolve_filter_base(field)[0] for field, _ in computed_filters]
        )
        select_field_names = self._ordered_unique(["accession", "version"] + computed_field_names)
        join_fields = set(select_field_names)
        join_fields.update(self._resolve_filter_base(field)[0] for field, _ in simple_filters)

        select_parts = [self._field_definition(name).select_sql(self) for name in select_field_names]
        select_parts.append(f"{json_path} AS compressed_seq")
        select_clause = ",\n        ".join(select_parts)

        cte_where: list[str] = []
        for field, value in simple_filters:
            self._append_filter_clause(cte_where, field=field, value=value, params=params, use_alias=False)

        query = (
            "WITH computed_sequences AS (\n"
            "    SELECT\n"
            f"        {select_clause}\n"
            f"    FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(join_fields, indent="        ")
        query += (
            "\n    WHERE organism = :organism"
            "\n      AND released_at IS NOT NULL"
            f"\n      AND {json_path} IS NOT NULL"
        )
        for clause in cte_where:
            query += f"\n      AND {clause}"
        query += "\n)\n"

        query += "SELECT \"accession\", \"version\", compressed_seq\nFROM computed_sequences"

        if computed_filters:
            query += "\nWHERE 1=1"
            computed_where: list[str] = []
            for field, value in computed_filters:
                self._append_filter_clause(
                    computed_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=True,
                )
            for clause in computed_where:
                query += f"\n  AND {clause}"

        query += "\nORDER BY \"accession\", \"version\""

        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset:
            query += f"\nOFFSET {offset}"

        return query, params

    # ------------------------------------------------------------------
    # Details queries
    # ------------------------------------------------------------------
    def build_details_query(
        self,
        selected_fields: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[str, dict[str, Any]]:
        params: dict[str, Any] = {"organism": self.organism}

        select_all = selected_fields is None
        fields = selected_fields or self._default_details_fields()
        fields = self._ordered_unique(fields)

        filter_base_fields = self._filter_base_fields()
        order_dependency_fields = self._order_dependency_fields()

        cte_candidate_fields = set(fields)
        cte_candidate_fields.update(filter_base_fields)
        cte_candidate_fields.update(order_dependency_fields)
        cte_candidate_fields.add("accession")  # ensure default ordering is supported

        if self._requires_cte(cte_candidate_fields):
            query = self._build_details_query_with_cte(
                params,
                fields,
                filter_base_fields,
                select_all,
                limit,
                offset,
            )
        else:
            query = self._build_details_query_simple(
                params,
                fields,
                limit,
                offset,
            )

        return query, params

    def _build_details_query_with_cte(
        self,
        params: dict[str, Any],
        fields: list[str],
        filter_base_fields: list[str],
        select_all: bool,
        limit: int | None,
        offset: int,
    ) -> str:
        cte_fields = self._ordered_unique(
            fields + filter_base_fields + self._order_dependency_fields() + ["accession"]
        )

        joins = self._collect_join_requirements(cte_fields)
        select_parts = [self._field_definition(field).select_sql(self) for field in cte_fields]
        select_clause = ",\n        ".join(select_parts)

        cte_where: list[str] = []
        outer_where: list[str] = []
        for field, value in self.filters.items():
            base_field, _ = self._resolve_filter_base(field)
            if self._field_definition(base_field).requires_cte:
                self._append_filter_clause(
                    outer_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=True,
                )
            else:
                self._append_filter_clause(
                    cte_where,
                    field=field,
                    value=value,
                    params=params,
                    use_alias=False,
                )

        query = (
            "WITH computed_fields AS (\n"
            "    SELECT\n"
            f"        {select_clause}\n"
            f"    FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(joins, indent="        ")
        query += (
            "\n    WHERE organism = :organism"
            "\n      AND released_at IS NOT NULL"
        )
        for clause in cte_where:
            query += f"\n      AND {clause}"
        query += "\n)\n"

        if select_all:
            outer_select = "*"
        else:
            outer_select = ", ".join(f'"{field}"' for field in fields)

        query += f"SELECT {outer_select}\nFROM computed_fields"

        if outer_where:
            query += "\nWHERE 1=1"
            for clause in outer_where:
                query += f"\n  AND {clause}"

        order_clause = self.build_order_by_clause("details")
        query += f"\nORDER BY {order_clause}"

        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset > 0:
            query += f"\nOFFSET {offset}"

        return query

    def _build_details_query_simple(
        self,
        params: dict[str, Any],
        fields: list[str],
        limit: int | None,
        offset: int,
    ) -> str:
        select_parts = [self._field_definition(field).select_sql(self) for field in fields]
        select_clause = ",\n        ".join(select_parts)

        join_fields = set(fields)
        join_fields.update(self._filter_base_fields())
        join_fields.update(self._order_dependency_fields())

        query = (
            "SELECT\n"
            f"        {select_clause}\n"
            f"FROM {BASE_TABLE}"
        )
        query += self._build_join_sql(join_fields, indent="    ")
        query += (
            "\nWHERE organism = :organism"
            "\n  AND released_at IS NOT NULL"
        )

        where_clauses: list[str] = []
        for field, value in self.filters.items():
            self._append_filter_clause(where_clauses, field=field, value=value, params=params, use_alias=False)

        for clause in where_clauses:
            query += f"\n  AND {clause}"

        order_clause = self.build_order_by_clause("details")
        query += f"\nORDER BY {order_clause}"

        if limit is not None:
            query += f"\nLIMIT {limit}"
        if offset > 0:
            query += f"\nOFFSET {offset}"

        return query

    # ------------------------------------------------------------------
    # Field-specific expression helpers
    # ------------------------------------------------------------------
    def _earliest_release_timestamp_expr(self) -> str:
        if not self.organism_config:
            return "released_at"

        earliest_config = self.organism_config.schema.get("earliestReleaseDate", {})
        if not earliest_config.get("enabled", False):
            return "released_at"

        external_fields = earliest_config.get("externalFields", [])
        parts = ["released_at"]
        for field in external_fields:
            json_key = field.replace("'", "''")
            parts.append(f"(joint_metadata -> 'metadata' ->> '{json_key}')::timestamp")

        least_expr = "LEAST(" + ", ".join(parts) + ")"
        window_min = (
            "MIN({least}) OVER (PARTITION BY {table}.accession "
            "ORDER BY version ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
        ).format(least=least_expr, table=BASE_TABLE)

        return f"LEAST({least_expr}, {window_min})"

    def _version_status_expression(self) -> str:
        return (
            "CASE\n"
            f"    WHEN version = MAX(version) OVER (PARTITION BY {BASE_TABLE}.accession) THEN 'LATEST_VERSION'\n"
            "    WHEN EXISTS (\n"
            "        SELECT 1 FROM sequence_entries_view sev2\n"
            "        WHERE sev2.accession = sequence_entries_view.accession\n"
            "          AND sev2.version > sequence_entries_view.version\n"
            "          AND sev2.is_revocation = true\n"
            "          AND sev2.organism = :organism\n"
            "          AND sev2.released_at IS NOT NULL\n"
            "    ) THEN 'REVOKED'\n"
            "    ELSE 'REVISED'\n"
            "END"
        )

    def _data_use_terms_expr(self) -> str:
        if not self.organism_config:
            return "'OPEN'"

        data_config = (self.organism_config.backend_config or {}).get("dataUseTerms", {})
        if not data_config.get("enabled", False):
            return "'OPEN'"

        return (
            "CASE\n"
            "    WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'\n"
            "         AND data_use_terms_table.restricted_until > NOW()\n"
            "    THEN 'RESTRICTED'\n"
            "    ELSE 'OPEN'\n"
            "END"
        )

    def _data_use_terms_restricted_until_expr(self) -> str:
        if not self.organism_config:
            return "NULL"

        data_config = (self.organism_config.backend_config or {}).get("dataUseTerms", {})
        if not data_config.get("enabled", False):
            return "NULL"

        return (
            "CASE\n"
            "    WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'\n"
            "         AND data_use_terms_table.restricted_until > NOW()\n"
            "    THEN TO_CHAR(data_use_terms_table.restricted_until, 'YYYY-MM-DD')\n"
            "    ELSE NULL\n"
            "END"
        )

    def _data_use_terms_url_expr(self) -> str:
        if not self.organism_config:
            return "NULL"

        backend_config = (self.organism_config.backend_config or {}).get("dataUseTerms", {})
        if not backend_config.get("enabled", False):
            return "NULL"

        urls = backend_config.get("urls", {})
        open_url = urls.get("open", "")
        restricted_url = urls.get("restricted", "")

        return (
            "CASE\n"
            "    WHEN data_use_terms_table.data_use_terms_type = 'RESTRICTED'\n"
            "         AND data_use_terms_table.restricted_until > NOW()\n"
            f"    THEN '{restricted_url}'\n"
            f"    ELSE '{open_url}'\n"
            "END"
        )

    def _default_details_fields(self) -> list[str]:
        fields = [
            "accession",
            "version",
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
        ]

        if self.organism_config and self.organism_config.schema:
            metadata_schema = self.organism_config.schema.get("metadata", [])
            for field_def in metadata_schema:
                name = field_def.get("name")
                if name:
                    fields.append(name)

        return self._ordered_unique(fields)


FIELD_DEFINITIONS: dict[str, FieldDefinition] = {
    "accession": FieldDefinition(
        name="accession",
        expression_factory=lambda _: f"{BASE_TABLE}.accession",
        order_base=(f"{BASE_TABLE}.accession",),
        order_alias=('"accession"',),
    ),
    "version": FieldDefinition(
        name="version",
        expression_factory=lambda _: "version",
        order_base=("version",),
        order_alias=('"version"',),
    ),
    "accessionVersion": FieldDefinition(
        name="accessionVersion",
        expression_factory=lambda _: f"{BASE_TABLE}.accession || '.' || {BASE_TABLE}.version",
        order_base=(f"{BASE_TABLE}.accession", "version"),
        order_alias=('"accession"', '"version"'),
        order_dependencies=("accession", "version"),
    ),
    "displayName": FieldDefinition(
        name="displayName",
        expression_factory=lambda _: f"{BASE_TABLE}.accession || '.' || {BASE_TABLE}.version",
        order_base=(f"{BASE_TABLE}.accession", "version"),
        order_alias=('"accession"', '"version"'),
        order_dependencies=("accession", "version"),
    ),
    "submittedDate": FieldDefinition(
        name="submittedDate",
        expression_factory=lambda _: "TO_CHAR(submitted_at, 'YYYY-MM-DD')",
    ),
    "submittedAtTimestamp": FieldDefinition(
        name="submittedAtTimestamp",
        expression_factory=lambda _: "EXTRACT(EPOCH FROM submitted_at)::bigint",
    ),
    "releasedDate": FieldDefinition(
        name="releasedDate",
        expression_factory=lambda _: "TO_CHAR(released_at, 'YYYY-MM-DD')",
    ),
    "releasedAtTimestamp": FieldDefinition(
        name="releasedAtTimestamp",
        expression_factory=lambda _: "EXTRACT(EPOCH FROM released_at)::bigint",
    ),
    "earliestReleaseDate": FieldDefinition(
        name="earliestReleaseDate",
        expression_factory=lambda builder: f"TO_CHAR({builder._earliest_release_timestamp_expr()}, 'YYYY-MM-DD')",
        requires_cte=True,
    ),
    "submissionId": FieldDefinition(
        name="submissionId",
        expression_factory=lambda _: "submission_id",
    ),
    "submitter": FieldDefinition(
        name="submitter",
        expression_factory=lambda _: "submitter",
    ),
    "groupId": FieldDefinition(
        name="groupId",
        expression_factory=lambda _: f"{BASE_TABLE}.group_id",
    ),
    "groupName": FieldDefinition(
        name="groupName",
        expression_factory=lambda _: "groups_table.group_name",
        joins=(JOIN_GROUPS,),
    ),
    "isRevocation": FieldDefinition(
        name="isRevocation",
        expression_factory=lambda _: "is_revocation",
    ),
    "versionComment": FieldDefinition(
        name="versionComment",
        expression_factory=lambda _: "version_comment",
    ),
    "versionStatus": FieldDefinition(
        name="versionStatus",
        expression_factory=lambda builder: builder._version_status_expression(),
        requires_cte=True,
    ),
    "dataUseTerms": FieldDefinition(
        name="dataUseTerms",
        expression_factory=lambda builder: builder._data_use_terms_expr(),
        joins=(JOIN_DATA_USE_TERMS,),
    ),
    "dataUseTermsRestrictedUntil": FieldDefinition(
        name="dataUseTermsRestrictedUntil",
        expression_factory=lambda builder: builder._data_use_terms_restricted_until_expr(),
        joins=(JOIN_DATA_USE_TERMS,),
    ),
    "dataUseTermsUrl": FieldDefinition(
        name="dataUseTermsUrl",
        expression_factory=lambda builder: builder._data_use_terms_url_expr(),
        joins=(JOIN_DATA_USE_TERMS,),
    ),
}
