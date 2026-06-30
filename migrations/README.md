# Migrations (scaffold)

For the PoC, the schema is created directly from the SQLAlchemy models via
`dealintel.database.init_db()` (called by `scripts/seed.py`). This is intentional
for a single-developer demo.

For production, schema evolution would move to **Alembic**:

```bash
alembic init migrations
# set sqlalchemy.url from DATABASE_URL, target_metadata = dealintel.orm.tables.Base.metadata
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

The ORM models in `dealintel/orm/tables.py` are already structured so
`--autogenerate` produces a clean initial migration. This directory is the
agreed home for those versions.
