"""Extend existing accounts without replacing user data or the publishing queue."""
from alembic import op
import sqlalchemy as sa
from app.models import TelegramDestinationChat, PublishingAttempt

revision = '0004_destinations'
down_revision = '0003_freshness'
branch_labels = depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = {c['name'] for c in sa.inspect(bind).get_columns('social_accounts')}
    columns = [
        sa.Column('connection_status', sa.String(32), nullable=False, server_default='CONNECTED'),
        sa.Column('publishing_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('capabilities', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('settings', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('last_verified_at', sa.DateTime(timezone=True), nullable=True),
    ]
    with op.batch_alter_table('social_accounts') as batch:
        for column in columns:
            if column.name not in existing:
                batch.add_column(column)
    TelegramDestinationChat.__table__.create(bind, checkfirst=True)
    PublishingAttempt.__table__.create(bind, checkfirst=True)
    if 'priority' not in {c['name'] for c in sa.inspect(bind).get_columns('jobs')}:
        with op.batch_alter_table('jobs') as batch:
            batch.add_column(sa.Column('priority', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    bind = op.get_bind()
    PublishingAttempt.__table__.drop(bind, checkfirst=True)
    TelegramDestinationChat.__table__.drop(bind, checkfirst=True)
    with op.batch_alter_table('jobs') as batch:
        batch.drop_column('priority')
    with op.batch_alter_table('social_accounts') as batch:
        for name in ('connection_status', 'publishing_enabled', 'capabilities', 'settings', 'last_verified_at'):
            batch.drop_column(name)
