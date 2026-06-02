"""Store thickness as numeric instead of string."""

from alembic import op
import sqlalchemy as sa


revision = 'a7b8c9d0e1f2'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        'generated_fg_items',
        'thickness',
        existing_type=sa.String(length=32),
        type_=sa.Numeric(10, 3),
        postgresql_using="NULLIF(TRIM(thickness), '')::numeric",
        existing_nullable=False,
    )
    op.alter_column(
        'item_master',
        'thickness',
        existing_type=sa.String(length=32),
        type_=sa.Numeric(10, 3),
        postgresql_using="NULLIF(TRIM(thickness), '')::numeric",
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'item_master',
        'thickness',
        existing_type=sa.Numeric(10, 3),
        type_=sa.String(length=32),
        postgresql_using='thickness::text',
        existing_nullable=True,
    )
    op.alter_column(
        'generated_fg_items',
        'thickness',
        existing_type=sa.Numeric(10, 3),
        type_=sa.String(length=32),
        postgresql_using='thickness::text',
        existing_nullable=False,
    )
