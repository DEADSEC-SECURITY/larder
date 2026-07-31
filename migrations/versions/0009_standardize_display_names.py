"""Standardize user-facing entity names to title case.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import re
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

APOSTROPHE_CONTRACTION = re.compile(r"'(S|T|Re|Ve|Ll|D|M)\b")


def _standardize_name(value: str) -> str:
    compact = " ".join(unicodedata.normalize("NFKC", value).split()).strip()
    titled = compact.lower().title()
    return APOSTROPHE_CONTRACTION.sub(lambda match: f"'{match.group(1).lower()}", titled)


def _standardize_column(table: str, key: str, column: str) -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT {key} AS row_key, {column} AS value FROM {table} WHERE {column} IS NOT NULL"
        )
    ).mappings()
    for row in rows:
        standardized = _standardize_name(row["value"])
        if standardized != row["value"]:
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :value WHERE {key} = :row_key"),
                {"value": standardized, "row_key": row["row_key"]},
            )


def upgrade() -> None:
    for table, key, column in (
        ("products", "barcode", "name"),
        ("products", "barcode", "brand"),
        ("brands", "id", "name"),
        ("brand_aliases", "id", "name"),
        ("stores", "id", "name"),
        ("food_categories", "id", "display_name"),
        ("store_routing_rules", "id", "name"),
        ("scanners", "id", "name"),
    ):
        _standardize_column(table, key, column)


def downgrade() -> None:
    # Original capitalization cannot be reconstructed after standardization.
    pass
