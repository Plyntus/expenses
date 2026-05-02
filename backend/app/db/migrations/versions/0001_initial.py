"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-02
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expenses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sheet_row_number", sa.Integer(), nullable=True),
        sa.Column("sheet_row_hash", sa.String(length=64), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=16), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("subcategory", sa.String(length=255), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("payment_method", sa.String(length=255), nullable=True),
        sa.Column("raw_values_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_expenses_category", "expenses", ["category"])
    op.create_index("ix_expenses_currency", "expenses", ["currency"])
    op.create_index("ix_expenses_date", "expenses", ["date"])
    op.create_index("ix_expenses_sheet_row_hash", "expenses", ["sheet_row_hash"])
    op.create_index("ix_expenses_sheet_row_number", "expenses", ["sheet_row_number"])
    op.create_index("ix_expenses_synced_at", "expenses", ["synced_at"])

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=False),
        sa.Column("rows_imported", sa.Integer(), nullable=False),
        sa.Column("rows_failed", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sync_runs_started_at", "sync_runs", ["started_at"])
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_status", table_name="sync_runs")
    op.drop_index("ix_sync_runs_started_at", table_name="sync_runs")
    op.drop_table("sync_runs")
    op.drop_index("ix_expenses_synced_at", table_name="expenses")
    op.drop_index("ix_expenses_sheet_row_number", table_name="expenses")
    op.drop_index("ix_expenses_sheet_row_hash", table_name="expenses")
    op.drop_index("ix_expenses_date", table_name="expenses")
    op.drop_index("ix_expenses_currency", table_name="expenses")
    op.drop_index("ix_expenses_category", table_name="expenses")
    op.drop_table("expenses")
