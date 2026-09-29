import pytest

from app.sql_guard import check_sql

TABLES = {"artist", "album", "track", "genre", "media_type", "playlist", "playlist_track",
          "invoice", "invoice_line", "customer", "employee"}


@pytest.mark.parametrize("sql", [
    "SELECT name AS \"Artist\" FROM artist LIMIT 10",
    "SELECT a.title, ar.name FROM album a JOIN artist ar ON ar.artist_id = a.artist_id",
    "WITH s AS (SELECT customer_id, SUM(total) t FROM invoice GROUP BY 1) "
    "SELECT c.first_name, s.t FROM s JOIN customer c USING (customer_id) ORDER BY s.t DESC",
    "SELECT EXTRACT(YEAR FROM invoice_date) y, COUNT(*) FROM invoice GROUP BY 1",
    "SELECT name FROM genre UNION SELECT name FROM media_type",
    "SELECT * FROM public.track WHERE name ILIKE '%love%';",
])
def test_allows_reads(sql):
    assert check_sql(sql, TABLES).ok, check_sql(sql, TABLES).reason


@pytest.mark.parametrize("sql", [
    "DELETE FROM artist",
    "UPDATE track SET unit_price = 0",
    "INSERT INTO genre VALUES (99, 'x')",
    "DROP TABLE invoice",
    "TRUNCATE invoice_line",
    "SELECT 1; DELETE FROM artist",
    "SELECT * INTO backup FROM artist",
    "WITH d AS (DELETE FROM artist RETURNING *) SELECT * FROM d",
    "SELECT * FROM track FOR UPDATE",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT set_config('statement_timeout', '0', false)",
    "SELECT current_setting('data_directory')",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.tables",
    "SELECT * FROM pg_user",
    "SELECT * FROM secrets",
    "COPY artist TO '/tmp/x'",
    "GRANT ALL ON artist TO public",
    "SET statement_timeout = 0",
    "BEGIN",
    "CREATE TABLE x (id int)",
    "ALTER TABLE artist ADD COLUMN x int",
    "",
    "not sql at all",
])
def test_rejects_non_reads(sql):
    assert not check_sql(sql, TABLES).ok
