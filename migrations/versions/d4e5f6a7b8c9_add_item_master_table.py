"""Add item_master table and backfill from generated items."""

from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'item_master',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('item_code', sa.String(length=128), nullable=False),
        sa.Column('item_name', sa.String(length=128), nullable=False),
        sa.Column('item_type', sa.String(length=16), nullable=False),
        sa.Column('parent_fg_code', sa.String(length=128), nullable=True),
        sa.Column('process_code', sa.String(length=32), nullable=True),
        sa.Column('material_type', sa.String(length=32), nullable=True),
        sa.Column('thickness', sa.String(length=32), nullable=True),
        sa.Column('coating', sa.String(length=16), nullable=True),
        sa.Column('pattern_id', sa.Integer(), nullable=True),
        sa.Column('bom_template_id', sa.Integer(), nullable=True),
        sa.Column('generated_fg_id', sa.Integer(), nullable=True),
        sa.Column('warehouse_code', sa.String(length=64), nullable=True),
        sa.Column('items_group_code', sa.Integer(), nullable=True),
        sa.Column('invntry_uom', sa.String(length=16), nullable=True),
        sa.Column('sal_unit_msr', sa.String(length=16), nullable=True),
        sa.Column('buy_unit_msr', sa.String(length=16), nullable=True),
        sa.Column('sales_item', sa.Boolean(), nullable=True),
        sa.Column('sap_pushed', sa.Boolean(), nullable=True),
        sa.Column('sap_pushed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['bom_template_id'], ['bom_templates.id']),
        sa.ForeignKeyConstraint(['generated_fg_id'], ['generated_fg_items.id']),
        sa.ForeignKeyConstraint(['pattern_id'], ['patterns.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('item_code'),
    )
    op.create_index('ix_item_master_item_code', 'item_master', ['item_code'], unique=False)
    op.create_index('ix_item_master_parent_fg_code', 'item_master', ['parent_fg_code'], unique=False)

    conn = op.get_bind()
    fg_rows = conn.execute(
        sa.text(
            'SELECT id, item_code, material_type, thickness, coating, pattern_id, '
            'bom_template_id, created_at FROM generated_fg_items'
        )
    ).fetchall()
    for fg in fg_rows:
        fg_id, fg_code, material, thickness, coating, pattern_id, template_id, created_at = fg
        item_name = f'{material} {thickness} {coating or ""} FG'.strip()
        conn.execute(
            sa.text(
                'INSERT INTO item_master (item_code, item_name, item_type, parent_fg_code, '
                'material_type, thickness, coating, pattern_id, bom_template_id, generated_fg_id, '
                'warehouse_code, items_group_code, invntry_uom, sal_unit_msr, buy_unit_msr, '
                'sales_item, sap_pushed, created_at, updated_at) '
                'VALUES (:item_code, :item_name, :item_type, NULL, :material_type, :thickness, '
                ':coating, :pattern_id, :bom_template_id, :generated_fg_id, :warehouse_code, '
                ':items_group_code, :invntry_uom, :sal_unit_msr, :buy_unit_msr, :sales_item, '
                '0, :created_at, :updated_at)'
            ),
            {
                'item_code': fg_code,
                'item_name': item_name[:128],
                'item_type': 'fg',
                'material_type': material,
                'thickness': thickness,
                'coating': coating,
                'pattern_id': pattern_id,
                'bom_template_id': template_id,
                'generated_fg_id': fg_id,
                'warehouse_code': 'FBD-FG',
                'items_group_code': 100,
                'invntry_uom': 'KGS',
                'sal_unit_msr': 'KGS',
                'buy_unit_msr': 'KGS',
                'sales_item': 1,
                'created_at': created_at or datetime.utcnow(),
                'updated_at': created_at or datetime.utcnow(),
            },
        )
        proc_rows = conn.execute(
            sa.text(
                'SELECT process_code, item_code, warehouse_code FROM generated_process_items '
                'WHERE fg_item_id = :fg_id'
            ),
            {'fg_id': fg_id},
        ).fetchall()
        for proc in proc_rows:
            process_code, proc_code, wh = proc
            wh = wh or _warehouse_for(process_code)
            conn.execute(
                sa.text(
                    'INSERT INTO item_master (item_code, item_name, item_type, parent_fg_code, '
                    'process_code, material_type, thickness, coating, pattern_id, bom_template_id, '
                    'generated_fg_id, warehouse_code, items_group_code, invntry_uom, sal_unit_msr, '
                    'buy_unit_msr, sales_item, sap_pushed, created_at, updated_at) '
                    'VALUES (:item_code, :item_name, :item_type, :parent_fg_code, :process_code, '
                    ':material_type, :thickness, :coating, :pattern_id, :bom_template_id, '
                    ':generated_fg_id, :warehouse_code, :items_group_code, :invntry_uom, '
                    ':sal_unit_msr, :buy_unit_msr, 0, 0, :created_at, :updated_at)'
                ),
                {
                    'item_code': proc_code,
                    'item_name': f'{proc_code} {process_code}'.strip()[:128],
                    'item_type': 'component',
                    'parent_fg_code': fg_code,
                    'process_code': process_code,
                    'material_type': material,
                    'thickness': thickness,
                    'coating': coating,
                    'pattern_id': pattern_id,
                    'bom_template_id': template_id,
                    'generated_fg_id': fg_id,
                    'warehouse_code': wh,
                    'items_group_code': 107,
                    'invntry_uom': 'KGS',
                    'sal_unit_msr': 'KGS',
                    'buy_unit_msr': 'KGS',
                    'created_at': created_at or datetime.utcnow(),
                    'updated_at': created_at or datetime.utcnow(),
                },
            )


def _warehouse_for(process_code):
    m = {
        'EMB': 'FBD-EMB',
        'MET': 'FBD-MTL',
        'SLT': 'FBD-SLT',
        'HRI': 'FBD-HRI',
        'COAT': 'FBD-COAT',
        'ALOX': 'FBD-ALOX',
        'FG': 'FBD-FG',
    }
    return m.get((process_code or '').upper(), 'FBD-RM')


def downgrade():
    op.drop_index('ix_item_master_parent_fg_code', table_name='item_master')
    op.drop_index('ix_item_master_item_code', table_name='item_master')
    op.drop_table('item_master')
