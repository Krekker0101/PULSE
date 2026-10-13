"""Repair universal fields skipped by the old 0002 early return, preserving data."""
from alembic import op
from app.schema_upgrades import ensure_universal_schema

revision = '0005_repair_schema'
down_revision = '0004_destinations'
branch_labels = depends_on = None


def upgrade():
    ensure_universal_schema(op)


def downgrade():
    # Additive repair: do not remove fields/data which may predate this revision.
    pass
