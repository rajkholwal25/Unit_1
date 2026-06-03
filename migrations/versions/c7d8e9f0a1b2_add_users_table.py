"""add users table (required before Unit 2 job tables)

Revision ID: c7d8e9f0a1b2
Revises: b8c9d0e1f2a3
Create Date: 2026-06-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b2'
down_revision = 'b8c9d0e1f2a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=True),
        sa.Column('email', sa.String(length=256), nullable=False),
        sa.Column('password_hash', sa.String(length=256), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False, server_default='viewer'),
        sa.Column('is_active_user', sa.Boolean(), nullable=True, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)


def downgrade():
    op.drop_index(op.f('ix_users_username'), table_name='users')
    op.drop_table('users')
