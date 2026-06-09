"""Add case-insensitive unique index on supplier + roll number for GRN batch dedup."""

from alembic import op


revision = 'h3i4j5k6l7m8'
down_revision = 'g2h3i4j5k6l7'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_roll_grn_supplier_roll_ci
        ON roll_grn_entry (lower(supplier_name), lower(supplier_roll_number))
        """
    )


def downgrade():
    op.execute('DROP INDEX IF EXISTS uq_roll_grn_supplier_roll_ci')
