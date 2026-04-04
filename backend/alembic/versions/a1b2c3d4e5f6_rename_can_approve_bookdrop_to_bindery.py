"""rename can_approve_bookdrop to can_approve_bindery

Revision ID: a1b2c3d4e5f6
Revises: fa9666098d96
Create Date: 2026-04-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'fa9666098d96'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('user_permissions', schema=None) as batch_op:
        batch_op.alter_column('can_approve_bookdrop', new_column_name='can_approve_bindery')


def downgrade() -> None:
    with op.batch_alter_table('user_permissions', schema=None) as batch_op:
        batch_op.alter_column('can_approve_bindery', new_column_name='can_approve_bookdrop')
