"""Add roll_grn_entry table for raw-material roll GRN numbers."""

from alembic import op
import sqlalchemy as sa


revision = 'g2h3i4j5k6l7'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'roll_grn_entry',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('grn_number', sa.String(length=16), nullable=False),
        sa.Column('supplier_name', sa.String(length=200), nullable=False),
        sa.Column('supplier_roll_number', sa.String(length=100), nullable=False),
        sa.Column('film_type', sa.String(length=50), nullable=False),
        sa.Column('coating', sa.String(length=50), nullable=False),
        sa.Column('width_mm', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('thickness_mic', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('length_mtr', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('gross_weight_kg', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('net_weight_kg', sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column('core_weight_kg', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('grn_number'),
    )
    op.create_index('ix_roll_grn_entry_grn_number', 'roll_grn_entry', ['grn_number'], unique=True)


def downgrade():
    op.drop_index('ix_roll_grn_entry_grn_number', table_name='roll_grn_entry')
    op.drop_table('roll_grn_entry')
