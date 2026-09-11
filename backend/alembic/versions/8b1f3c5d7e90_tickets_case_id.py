"""tickets.case_id -> after_sales_cases.id

Phase 9D links an after-sales support ticket to the case it was raised for.
The column is nullable because ordinary support tickets have no case, and it is
indexed because the treatment service resolves "does this case already have a
ticket?" through it (idempotency).

Revision ID: 8b1f3c5d7e90
Revises: 4c7f9a1b2d3e
Create Date: 2026-09-11 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b1f3c5d7e90'
down_revision: Union[str, None] = '4c7f9a1b2d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK_NAME = 'fk_tickets_case_id_after_sales_cases'
_IX_NAME = 'ix_tickets_case_id'


def upgrade() -> None:
    # batch_alter_table keeps this SQLite- and PostgreSQL-compatible: SQLite
    # cannot ALTER TABLE ADD CONSTRAINT, so batch mode recreates the table with
    # the new column, FK and index in one step.
    with op.batch_alter_table('tickets') as batch_op:
        batch_op.add_column(sa.Column('case_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(_FK_NAME, 'after_sales_cases', ['case_id'], ['id'])
        batch_op.create_index(_IX_NAME, ['case_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('tickets') as batch_op:
        batch_op.drop_index(_IX_NAME)
        # Dropping the column also removes the FK on PostgreSQL, and batch mode
        # removes it from the recreated SQLite table (SQLite reports foreign
        # keys without names, so the constraint is never named here).
        batch_op.drop_column('case_id')