"""add_book_tags

Revision ID: c3f1d8a92e10
Revises: b87f11474be0
Create Date: 2026-03-27

"""
from alembic import op
import sqlalchemy as sa

revision = 'c3f1d8a92e10'
down_revision = 'b87f11474be0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'book_tags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('book_id', sa.Integer(), sa.ForeignKey('books.id'), nullable=False),
        sa.Column('tag', sa.Text(), nullable=False),
        sa.Column('source', sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_book_tags_id', 'book_tags', ['id'])
    op.create_index('ix_book_tags_book_id', 'book_tags', ['book_id'])


def downgrade() -> None:
    op.drop_index('ix_book_tags_book_id', 'book_tags')
    op.drop_index('ix_book_tags_id', 'book_tags')
    op.drop_table('book_tags')
