"""BOM builder: yield, raw material, line quantities."""

from alembic import op
import sqlalchemy as sa


revision = 'b8c9d0e1f2a3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('generated_fg_items', sa.Column('raw_material_item_code', sa.String(length=128), nullable=True))
    op.add_column('generated_fg_items', sa.Column('yield_loss_pct', sa.Numeric(5, 2), nullable=False, server_default='2'))
    op.add_column('generated_fg_items', sa.Column('sap_bom_pushed_at', sa.DateTime(), nullable=True))
    op.add_column('bom_structures', sa.Column('line_type', sa.String(length=16), nullable=False, server_default='process'))
    op.add_column('bom_structures', sa.Column('quantity', sa.Numeric(12, 6), nullable=True))
    op.add_column('bom_structures', sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('bom_structures', 'sort_order')
    op.drop_column('bom_structures', 'quantity')
    op.drop_column('bom_structures', 'line_type')
    op.drop_column('generated_fg_items', 'sap_bom_pushed_at')
    op.drop_column('generated_fg_items', 'yield_loss_pct')
    op.drop_column('generated_fg_items', 'raw_material_item_code')
