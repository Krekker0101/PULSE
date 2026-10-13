"""Additive repair for databases partially upgraded by early PULSE releases."""
import sqlalchemy as sa


def ensure_universal_schema(op):
    bind = op.get_bind()
    columns = {
        'sources': [
            sa.Column('platform', sa.String(32), nullable=False, server_default='telegram'),
            sa.Column('bound_account_id', sa.Integer(), nullable=True),
            sa.Column('external_id', sa.String(200), nullable=True),
            sa.Column('canonical_url', sa.Text(), nullable=False, server_default=''),
            sa.Column('provider_state', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('scan_error', sa.Text(), nullable=False, server_default=''),
        ],
        'videos': [
            sa.Column('remote_video_id', sa.String(200), nullable=True),
            sa.Column('source_url', sa.Text(), nullable=False, server_default=''),
            sa.Column('thumbnail_url', sa.Text(), nullable=False, server_default=''),
        ],
    }
    for table, additions in columns.items():
        existing = {c['name'] for c in sa.inspect(bind).get_columns(table)}
        missing = [c for c in additions if c.name not in existing]
        if missing:
            with op.batch_alter_table(table) as batch:
                for column in missing:
                    batch.add_column(column)
    for table, name, fields, unique in (
        ('sources', 'ix_sources_platform', ['platform'], False),
        ('videos', 'ix_video_remote_source', ['source_id', 'remote_video_id'], True),
    ):
        indexes = sa.inspect(bind).get_indexes(table)
        if not any(i['name'] == name or (i['column_names'] == fields and bool(i['unique']) == unique) for i in indexes):
            op.create_index(name, table, fields, unique=unique)
    foreign_keys = sa.inspect(bind).get_foreign_keys('sources')
    if not any(fk['constrained_columns'] == ['bound_account_id'] for fk in foreign_keys):
        with op.batch_alter_table('sources') as batch:
            batch.create_foreign_key('fk_sources_bound_social_account', 'social_accounts', ['bound_account_id'], ['id'])
    if 'dialog_states' not in sa.inspect(bind).get_table_names():
        op.create_table('dialog_states',
            sa.Column('identity', sa.String(256), primary_key=True),
            sa.Column('state', sa.String(256), nullable=True),
            sa.Column('data', sa.JSON(), nullable=False, server_default='{}'),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    op.execute("UPDATE sources SET provider_state = '{}' WHERE provider_state IS NULL")
