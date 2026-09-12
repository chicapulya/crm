from places_census.storage import db
conn = db.get_connection()

print("=== Total calls by category ===")
rows = conn.execute(
    "SELECT category, COUNT(*) as calls, SUM(result_count) as results "
    "FROM api_calls GROUP BY category ORDER BY calls DESC"
).fetchall()
for r in rows:
    print(dict(r))

print(f"\nTotal calls: {conn.execute('SELECT COUNT(*) FROM api_calls').fetchone()[0]}")
print(f"Total places: {conn.execute('SELECT COUNT(*) FROM places').fetchone()[0]}")
