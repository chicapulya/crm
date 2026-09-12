import sqlite3
conn = sqlite3.connect(str(__import__('places_census.config', fromlist=['DB_PATH']).DB_PATH))
rows = conn.execute("""
    SELECT strftime('%Y-%m', ts) AS month,
           sku,
           COUNT(*) AS used
    FROM api_calls
    WHERE from_cache = 0
    GROUP BY month, sku
""").fetchall()
print("month       sku        used")
print("-" * 30)
for r in rows:
    print(f"{str(r[0]):<12}{str(r[1]):<10}{r[2]:>6}")
conn.close()
