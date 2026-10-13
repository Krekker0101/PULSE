"""YouTube API data freshness tracking.

Revision ID: 0003_freshness
Revises: 0002_universal
"""
from alembic import op
import sqlalchemy as sa
revision = '0003_freshness'
down_revision = '0002_universal'
branch_labels = None
depends_on = None


def upgrade():
    cols={column['name'] for column in sa.inspect(op.get_bind()).get_columns('videos')}
    if 'metadata_checked_at' not in cols:
        with op.batch_alter_table('videos') as batch:
            batch.add_column(sa.Column('metadata_checked_at',sa.DateTime(timezone=True),nullable=True))


def downgrade():
    cols={column['name'] for column in sa.inspect(op.get_bind()).get_columns('videos')}
    if 'metadata_checked_at' in cols:
        with op.batch_alter_table('videos') as batch:
            batch.drop_column('metadata_checked_at')
