"""after_sales_cases table

Revision ID: 4c7f9a1b2d3e
Revises: 7a9c1e4b8d2f
Create Date: 2026-09-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4c7f9a1b2d3e'
down_revision: Union[str, None] = '7a9c1e4b8d2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('after_sales_cases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('case_id', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('case_type', sa.Enum('QUALITY_ISSUE', 'LOGISTICS_DISPUTE', 'OTHER', name='after_sales_case_type', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('requested_action', sa.Enum('REFUND', 'EXCHANGE', 'REPAIR', 'UNKNOWN', name='after_sales_requested_action', native_enum=False, create_constraint=True, length=20), nullable=False),
    sa.Column('problem_description', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('INFORMATION_COLLECTION', 'ELIGIBILITY_CHECK', 'PROCESSING', 'PENDING_HUMAN', 'COMPLETED', 'REJECTED', name='after_sales_case_status', native_enum=False, create_constraint=True, length=30), nullable=False),
    sa.Column('risk_level', sa.String(length=20), nullable=False),
    sa.Column('collected_information', sa.JSON(), nullable=True),
    sa.Column('missing_information', sa.JSON(), nullable=True),
    sa.Column('ai_summary', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_after_sales_cases_case_id'), 'after_sales_cases', ['case_id'], unique=True)
    op.create_index(op.f('ix_after_sales_cases_order_id'), 'after_sales_cases', ['order_id'], unique=False)
    op.create_index(op.f('ix_after_sales_cases_status'), 'after_sales_cases', ['status'], unique=False)
    op.create_index(op.f('ix_after_sales_cases_user_id'), 'after_sales_cases', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_after_sales_cases_user_id'), table_name='after_sales_cases')
    op.drop_index(op.f('ix_after_sales_cases_status'), table_name='after_sales_cases')
    op.drop_index(op.f('ix_after_sales_cases_order_id'), table_name='after_sales_cases')
    op.drop_index(op.f('ix_after_sales_cases_case_id'), table_name='after_sales_cases')
    op.drop_table('after_sales_cases')
