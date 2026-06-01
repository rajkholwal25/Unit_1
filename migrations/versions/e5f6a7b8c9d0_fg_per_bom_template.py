"""Same FG + different BOM templates; BOM structures scoped per saved variant."""

from alembic import op
import sqlalchemy as sa


revision = 'e5f6a7b8c9d0'
down_revision = 'd4e5f6a7b8c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('generated_fg_items', schema=None) as batch_op:
        batch_op.drop_constraint('generated_fg_items_item_code_key', type_='unique')
        batch_op.create_unique_constraint(
            'uq_fg_item_code_template',
            ['item_code', 'bom_template_id'],
        )

    op.add_column(
        'bom_structures',
        sa.Column('generated_fg_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_bom_structures_generated_fg_id',
        'bom_structures',
        'generated_fg_items',
        ['generated_fg_id'],
        ['id'],
    )

    conn = op.get_bind()
    for sid, parent_code in conn.execute(
        sa.text('SELECT id, parent_item_code FROM bom_structures WHERE generated_fg_id IS NULL')
    ).fetchall():
        fg_rows = conn.execute(
            sa.text('SELECT id FROM generated_fg_items WHERE item_code = :code ORDER BY id'),
            {'code': parent_code},
        ).fetchall()
        if len(fg_rows) == 1:
            conn.execute(
                sa.text('UPDATE bom_structures SET generated_fg_id = :fg_id WHERE id = :sid'),
                {'fg_id': fg_rows[0][0], 'sid': sid},
            )


def downgrade():
    op.drop_constraint('fk_bom_structures_generated_fg_id', 'bom_structures', type_='foreignkey')
    op.drop_column('bom_structures', 'generated_fg_id')
    with op.batch_alter_table('generated_fg_items', schema=None) as batch_op:
        batch_op.drop_constraint('uq_fg_item_code_template', type_='unique')
        batch_op.create_unique_constraint('generated_fg_items_item_code_key', ['item_code'])
