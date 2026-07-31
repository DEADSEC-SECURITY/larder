"""Notify on nutrition writes.

product_nutrition was missing from the 0003 watched set; enrichment usually
also touches the products row, but a nutrition-only update should push a live
refresh too.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS larder_notify_product_nutrition ON product_nutrition")
    op.execute(
        """
        CREATE TRIGGER larder_notify_product_nutrition
        AFTER INSERT OR UPDATE ON product_nutrition
        FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS larder_notify_product_nutrition ON product_nutrition")
