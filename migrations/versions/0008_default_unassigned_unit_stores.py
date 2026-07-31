"""Default unassigned inventory units from store-routing rules.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT
                item.id AS item_id,
                matched.store_id,
                matched.rule_id,
                matched.match_type
            FROM inventory_items AS item
            JOIN LATERAL (
                SELECT
                    rule.store_id,
                    rule.id AS rule_id,
                    rule.match_type,
                    rule.priority
                FROM store_routing_rules AS rule
                JOIN stores AS store
                  ON store.id = rule.store_id
                 AND store.active = true
                WHERE rule.enabled = true
                  AND (
                    (rule.match_type = 'PRODUCT'
                     AND rule.product_barcode = item.product_barcode)
                    OR
                    (rule.match_type = 'BRAND'
                     AND EXISTS (
                        SELECT 1
                        FROM product_brands AS product_brand
                        WHERE product_brand.product_barcode = item.product_barcode
                          AND product_brand.brand_id = rule.brand_id
                     ))
                    OR
                    (rule.match_type = 'CATEGORY'
                     AND EXISTS (
                        SELECT 1
                        FROM product_categories AS product_category
                        WHERE product_category.product_barcode = item.product_barcode
                          AND product_category.category_id = rule.category_id
                     ))
                  )
                ORDER BY
                    CASE rule.match_type
                        WHEN 'PRODUCT' THEN 3
                        WHEN 'BRAND' THEN 2
                        ELSE 1
                    END DESC,
                    rule.priority DESC,
                    rule.id::text DESC
                LIMIT 1
            ) AS matched ON true
            WHERE item.status = 'IN_STOCK'
              AND item.store_id IS NULL
            FOR UPDATE OF item
            """
        )
    ).mappings()

    for row in rows:
        bind.execute(
            sa.text("UPDATE inventory_items SET store_id = :store_id WHERE id = :item_id"),
            {"store_id": row["store_id"], "item_id": row["item_id"]},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO inventory_events (
                    id,
                    inventory_item_id,
                    event_type,
                    occurred_at,
                    scan_event_id,
                    actor_type,
                    actor_id,
                    before_state,
                    after_state,
                    reason
                ) VALUES (
                    :id,
                    :item_id,
                    'STORE_CHANGED',
                    now(),
                    NULL,
                    'SYSTEM',
                    'store-routing',
                    CAST(:before_state AS jsonb),
                    CAST(:after_state AS jsonb),
                    :reason
                )
                """
            ),
            {
                "id": uuid4(),
                "item_id": row["item_id"],
                "before_state": json.dumps({"store_id": None}),
                "after_state": json.dumps(
                    {
                        "store_id": str(row["store_id"]),
                        "rule_id": str(row["rule_id"]),
                        "source": f"{row['match_type'].lower()}_rule",
                    }
                ),
                "reason": f"Defaulted from {row['match_type'].title()} rule",
            },
        )


def downgrade() -> None:
    # Store assignments may have been edited after this migration. They cannot
    # be safely distinguished and reverted without destroying user data.
    pass
