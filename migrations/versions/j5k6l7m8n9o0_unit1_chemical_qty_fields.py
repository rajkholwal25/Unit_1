"""Unit 1: chemical qty snapshot + chemical SAP item code on detail line."""

from alembic import op
import sqlalchemy as sa


revision = 'j5k6l7m8n9o0'
down_revision = 'i4j5k6l7m8n9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('job_detail_line', sa.Column('chemical_item_code', sa.String(50), nullable=True))
    op.add_column('job_detail_line', sa.Column('chemical_qty_kg', sa.Numeric(10, 3), nullable=True))
    op.add_column('job_detail_line', sa.Column('metallisation_qty_kg', sa.Numeric(10, 3), nullable=True))


def downgrade():
    op.drop_column('job_detail_line', 'metallisation_qty_kg')
    op.drop_column('job_detail_line', 'chemical_qty_kg')
    op.drop_column('job_detail_line', 'chemical_item_code')
