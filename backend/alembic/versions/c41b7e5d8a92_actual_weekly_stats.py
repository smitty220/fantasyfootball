"""actual weekly stats

Revision ID: c41b7e5d8a92
Revises: b937206114ea
Create Date: 2026-09-09 21:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41b7e5d8a92'
down_revision: Union[str, Sequence[str], None] = 'b937206114ea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('actual_stats',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('player_id', sa.Integer(), nullable=False),
    sa.Column('season', sa.Integer(), nullable=False),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('stat_json', sa.JSON(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('player_id', 'season', 'week', name='uq_actual_stat')
    )
    op.create_index(op.f('ix_actual_stats_player_id'), 'actual_stats', ['player_id'], unique=False)
    op.create_index(op.f('ix_actual_stats_season'), 'actual_stats', ['season'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_actual_stats_season'), table_name='actual_stats')
    op.drop_index(op.f('ix_actual_stats_player_id'), table_name='actual_stats')
    op.drop_table('actual_stats')
