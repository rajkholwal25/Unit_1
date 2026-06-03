"""Add yield_loss_pct to job_detail_line (Unit 1 replaces UPS for RM gross-up)."""

from alembic import op
import sqlalchemy as sa


revision = 'f1a2b3c4d5e6'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE job_detail_line "
        "ADD COLUMN IF NOT EXISTS yield_loss_pct NUMERIC(5, 2) DEFAULT 2"
    )


def downgrade():
    op.drop_column('job_detail_line', 'yield_loss_pct')
