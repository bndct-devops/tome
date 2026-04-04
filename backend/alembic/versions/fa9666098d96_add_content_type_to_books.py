"""add content_type to books

Revision ID: fa9666098d96
Revises: 51532e331a2f
Create Date: 2026-04-03 10:34:27.521668

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fa9666098d96'
down_revision: Union[str, Sequence[str], None] = '51532e331a2f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.add_column(sa.Column('content_type', sa.String(length=16), server_default='volume', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('books', schema=None) as batch_op:
        batch_op.drop_column('content_type')
