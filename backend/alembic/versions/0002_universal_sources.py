"""Universal providers, YouTube identifiers, durable Telegram dialog storage.

Revision ID: 0002_universal
Revises: 0001_initial
"""
from alembic import op
revision = '0002_universal'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    from app.schema_upgrades import ensure_universal_schema
    ensure_universal_schema(op)


def downgrade():
    op.drop_table('dialog_states')
    op.drop_index('ix_video_remote_source', table_name='videos')
    with op.batch_alter_table('videos') as batch:
        batch.drop_column('thumbnail_url'); batch.drop_column('source_url'); batch.drop_column('remote_video_id')
    with op.batch_alter_table('sources') as batch:
        batch.drop_index('ix_sources_platform')
        for name in ('bound_account_id','scan_error','provider_state','canonical_url','external_id','platform'):
            batch.drop_column(name)
