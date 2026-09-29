"""Schema catalog, loaded from information_schema at startup.

Only the planner (SQL generation agent) ever sees column-level detail, and only
through the describe_tables tool. The gate sees table names and descriptions.
Nothing here is ever returned to the client.
"""
from dataclasses import dataclass, field

# Plain-language descriptions help both the gate and the planner pick tables.
TABLE_DESCRIPTIONS = {
    "artist": "Music artists.",
    "album": "Albums; each album belongs to one artist.",
    "track": "Songs/tracks: album, genre, media type, composer, duration "
             "(milliseconds), file size (bytes) and unit price.",
    "genre": "Music genres.",
    "media_type": "File formats of tracks (e.g. MPEG audio, AAC, protected video).",
    "playlist": "Named playlists.",
    "playlist_track": "Which tracks are in which playlists.",
    "invoice": "Customer purchases (sales): invoice date, billing address and total amount.",
    "invoice_line": "Line items of an invoice: the track bought, its unit price and quantity.",
    "customer": "Store customers: name, company, address, contact details, "
                "and their support representative (an employee).",
    "employee": "Store employees: title, hire date, contact details, and who they report to.",
}


@dataclass
class Table:
    name: str
    description: str
    columns: list[tuple[str, str, bool]] = field(default_factory=list)  # name, type, nullable
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[tuple[str, str, str]] = field(default_factory=list)  # col, ref_table, ref_col


class SchemaCatalog:
    def __init__(self, tables: dict[str, Table]):
        self.tables = tables

    @property
    def table_names(self) -> set[str]:
        return set(self.tables)

    @property
    def column_names(self) -> set[str]:
        return {c[0] for t in self.tables.values() for c in t.columns}

    def overview(self) -> str:
        """Table names and descriptions only."""
        return "\n".join(f"- {t.name}: {t.description}" for t in sorted(self.tables.values(), key=lambda t: t.name))

    def describe(self, names: list[str]) -> str:
        out, unknown = [], []
        for raw in names:
            t = self.tables.get(raw.strip().lower())
            if t is None:
                unknown.append(raw)
                continue
            cols = "\n".join(
                f"    {c} {typ}{'' if nullable else ' NOT NULL'}" for c, typ, nullable in t.columns
            )
            lines = [f"TABLE {t.name} -- {t.description}", cols]
            if t.primary_key:
                lines.append(f"    PRIMARY KEY ({', '.join(t.primary_key)})")
            for col, rt, rc in t.foreign_keys:
                lines.append(f"    FOREIGN KEY ({col}) REFERENCES {rt}({rc})")
            out.append("\n".join(lines))
        if unknown:
            out.append(f"Unknown tables: {', '.join(unknown)}. Available: {', '.join(sorted(self.tables))}")
        return "\n\n".join(out)


_COLUMNS_SQL = """
SELECT c.table_name, c.column_name,
       CASE WHEN c.data_type = 'character varying' THEN 'VARCHAR(' || c.character_maximum_length || ')'
            WHEN c.data_type = 'numeric' THEN 'NUMERIC(' || c.numeric_precision || ',' || c.numeric_scale || ')'
            ELSE upper(c.data_type) END,
       c.is_nullable = 'YES'
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = 'public' AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_name, c.ordinal_position
"""

_CONSTRAINTS_SQL = """
SELECT tc.table_name, tc.constraint_type, kcu.column_name,
       ccu.table_name AS ref_table, ccu.column_name AS ref_column
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
LEFT JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name AND tc.constraint_type = 'FOREIGN KEY'
WHERE tc.table_schema = 'public' AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
ORDER BY tc.table_name, kcu.ordinal_position
"""


def load_catalog(conn) -> SchemaCatalog:
    tables: dict[str, Table] = {}
    with conn.cursor() as cur:
        cur.execute(_COLUMNS_SQL)
        for table, col, typ, nullable in cur.fetchall():
            t = tables.setdefault(table, Table(table, TABLE_DESCRIPTIONS.get(table, "")))
            t.columns.append((col, typ, nullable))
        cur.execute(_CONSTRAINTS_SQL)
        for table, ctype, col, ref_table, ref_col in cur.fetchall():
            t = tables.get(table)
            if t is None:
                continue
            if ctype == "PRIMARY KEY":
                t.primary_key.append(col)
            else:
                t.foreign_keys.append((col, ref_table, ref_col))
    conn.rollback()
    if not tables:
        raise RuntimeError("No tables visible to the read-only role")
    return SchemaCatalog(tables)
