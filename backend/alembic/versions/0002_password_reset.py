"""add email + password_reset table

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-29

Tables: adds user.email (nullable + unique) and a new password_reset table
(hashed, single-use reset tokens). See app/models.py for the SQLModel source.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # user.email — nullable + unique (SQLite allows multiple NULLs, so existing
    # accounts with no email don't collide).
    op.add_column("user", sa.Column("email", sa.String, nullable=True))
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    # password_reset — hashed single-use tokens (sha256(token).hexdigest()).
    op.create_table(
        "password_reset",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
        sa.Column("used_at", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False),
    )
    op.create_index("ix_password_reset_user_id", "password_reset", ["user_id"])
    op.create_index("ix_password_reset_token_hash", "password_reset", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_password_reset_token_hash", table_name="password_reset")
    op.drop_index("ix_password_reset_user_id", table_name="password_reset")
    op.drop_table("password_reset")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_column("user", "email")
