"""Unit 1 RM calc: chemical coating gsm, metallisation gsm, thickness mic on detail line."""

from alembic import op
import sqlalchemy as sa


revision = 'i4j5k6l7m8n9'
down_revision = 'h3i4j5k6l7m8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'job_detail_line',
        sa.Column('chemical_coating_gsm', sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        'job_detail_line',
        sa.Column('metallisation_gsm', sa.Numeric(8, 3), nullable=True),
    )
    op.add_column(
        'job_detail_line',
        sa.Column('thickness_mic', sa.Numeric(6, 2), nullable=True),
    )


def downgrade():
    op.drop_column('job_detail_line', 'thickness_mic')
    op.drop_column('job_detail_line', 'metallisation_gsm')
    op.drop_column('job_detail_line', 'chemical_coating_gsm')
