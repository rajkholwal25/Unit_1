"""Add coating_types table for manageable coating codes."""

from alembic import op
import sqlalchemy as sa


revision = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None

DEFAULT_COATINGS = [
    ('TR', 'TR'),
    ('NTR', 'NTR'),
    ('CF', 'CF'),
    ('HF', 'HF'),
    ('HRI', 'HRI'),
    ('ALO', 'ALO'),
]


def upgrade():
    op.create_table(
        'coating_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=16), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code'),
    )

    table = sa.table(
        'coating_types',
        sa.column('code', sa.String),
        sa.column('name', sa.String),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(
        table,
        [{'code': code, 'name': name, 'is_active': True} for code, name in DEFAULT_COATINGS],
    )


def downgrade():
    op.drop_table('coating_types')
