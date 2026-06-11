# Mined Database & Migration Patterns

DB/migration patterns mined from the full human review history.

## Migration DDL must match the ORM model's resolved dialect type

A migration creating `sa.JSON()` where the model's type resolves to postgres JSONB produces schemas that differ between fresh installs and migrated databases, loses dialect features (JSONB operators/indexing), and forces a corrective migration later — a mismatch this repo has already had to fix once. Check new columns against the model's type map, especially dialect-resolving types.

```python
# WRONG
sa.Column("config", sa.JSON())

# CORRECT (model uses JSONType -> JSONB on postgres)
sa.Column("config", postgresql.JSONB())
```

*Flagged by reviewers in: #1312*

## Backfilled data must satisfy every constraint the same migration adds

A backfill that writes sentinel values for orphan rows and then creates an FK or constraint those rows violate passes on clean dev databases and fails mid-deploy on production data, leaving the schema half-migrated. Delete the orphans (the parent is gone), or create the constraint NOT VALID and validate separately; test the migration against dirty data containing orphans.

```python
# WRONG: orphans get principal_id='unknown', then a composite FK to creatives fails on them

# CORRECT
op.execute("DELETE FROM creative_reviews WHERE creative_id NOT IN (SELECT creative_id FROM creatives)")
op.create_foreign_key(...)
```

*Flagged by reviewers in: #1071*
