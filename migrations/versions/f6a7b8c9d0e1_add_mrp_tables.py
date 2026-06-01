"""Add MRP runs and recommendations tables."""

from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mrp_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='running'),
        sa.Column('horizon_days', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'mrp_recommendations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.Integer(), nullable=False),
        sa.Column('item_code', sa.String(length=128), nullable=False),
        sa.Column('item_name', sa.String(length=256), nullable=True),
        sa.Column('recommendation_type', sa.String(length=32), nullable=False, server_default='production'),
        sa.Column('quantity', sa.Float(), nullable=False),
        sa.Column('uom', sa.String(length=16), nullable=True),
        sa.Column('warehouse_code', sa.String(length=64), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('demand_source', sa.String(length=256), nullable=True),
        sa.Column('on_hand_qty', sa.Float(), nullable=True),
        sa.Column('demand_qty', sa.Float(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('sap_production_order_entry', sa.Integer(), nullable=True),
        sa.Column('sap_production_order_number', sa.Integer(), nullable=True),
        sa.Column('accepted_at', sa.DateTime(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['run_id'], ['mrp_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_mrp_recommendations_run_id', 'mrp_recommendations', ['run_id'])
    op.create_index('ix_mrp_recommendations_status', 'mrp_recommendations', ['status'])


def downgrade():
    op.drop_index('ix_mrp_recommendations_status', table_name='mrp_recommendations')
    op.drop_index('ix_mrp_recommendations_run_id', table_name='mrp_recommendations')
    op.drop_table('mrp_recommendations')
    op.drop_table('mrp_runs')
