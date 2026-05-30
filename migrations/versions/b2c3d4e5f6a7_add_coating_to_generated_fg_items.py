"""Add coating column to generated_fg_items."""

from alembic import op
import sqlalchemy as sa


revision = 'b2c3d4e5f6a7'
down_revision = '956e43a33559'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'generated_fg_items',
        sa.Column('coating', sa.String(length=16), nullable=True),
    )


def downgrade():
    op.drop_column('generated_fg_items', 'coating')
