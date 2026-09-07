"""approval_requests table

Revision ID: 7a9c1e4b8d2f
Revises: 018c7c0772c7
Create Date: 2026-09-07 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a9c1e4b8d2f'
down_revision: Union[str, None] = '018c7c0772c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('approval_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=False),
    sa.Column('tool_name', sa.String(length=100), nullable=False),
    sa.Column('tool_arguments', sa.JSON(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('risk_level', sa.String(length=20), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='approval_status', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approval_requests_request_id'), 'approval_requests', ['request_id'], unique=False)
    op.create_index(op.f('ix_approval_requests_user_id'), 'approval_requests', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_approval_requests_user_id'), table_name='approval_requests')
    op.drop_index(op.f('ix_approval_requests_request_id'), table_name='approval_requests')
    op.drop_table('approval_requests')
