from places_census.storage import db
conn = db.get_connection()
for r in conn.execute("SELECT * FROM budget_ledger"):
    print(dict(r))
