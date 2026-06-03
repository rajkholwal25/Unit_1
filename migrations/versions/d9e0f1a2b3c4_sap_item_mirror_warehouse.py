"""sap_item_mirror default_warehouse

Revision ID: d9e0f1a2b3c4
Revises: bb8141a0713b
Create Date: 2026-06-03 12:25:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd9e0f1a2b3c4'
down_revision = 'bb8141a0713b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'sap_item_mirror',
        sa.Column('default_warehouse', sa.String(length=20), nullable=True),
    )
    op.create_index(
        op.f('ix_sap_item_mirror_default_warehouse'),
        'sap_item_mirror',
        ['default_warehouse'],
        unique=False,
    )


def downgrade():
    op.drop_index(op.f('ix_sap_item_mirror_default_warehouse'), table_name='sap_item_mirror')
    op.drop_column('sap_item_mirror', 'default_warehouse')
