# Skill: setup-db

Create the `polycode` PostgreSQL database and all tables (run once).

```bash
cd c:\Users\HP\Desktop\Upwork_project\modeling\polycode
.venv\Scripts\activate
python scripts/setup_polycode_db.py
```

Creates: `runs`, `trades`, `pnl_snapshots` tables in the `polycode` database.
