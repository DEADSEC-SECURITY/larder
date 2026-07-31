"""Add trip price, manual dismissal, and scanned members to shopping rows.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

TABLES = ("grocery_list_items", "stock_group_shopping_items")
COLUMNS = {
    "unit_price": sa.Column("unit_price", sa.Numeric(12, 2)),
    "dismissed_at": sa.Column("dismissed_at", sa.DateTime(timezone=True)),
    "scanned_barcodes": sa.Column(
        "scanned_barcodes",
        postgresql.JSONB(astext_type=sa.Text()),
        server_default=sa.text("'[]'::jsonb"),
        nullable=False,
    ),
}


def upgrade() -> None:
    # Migration 0001 runs create_all from the current models, so a fresh
    # database already has these columns.
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, column in COLUMNS.items():
            if name not in existing:
                op.add_column(table, column.copy())


def downgrade() -> None:
    for table in TABLES:
        for name in COLUMNS:
            op.drop_column(table, name)
