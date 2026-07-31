"""Product archiving for the web UI.

Products with inventory history cannot be hard-deleted because inventory
events are append-only; deleting from the UI archives them instead. The
products notify trigger also gains DELETE so hard deletes push live updates.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("products")}
    if "archived_at" not in columns:
        op.add_column(
            "products", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.execute("DROP TRIGGER IF EXISTS larder_notify_products ON products")
    op.execute(
        """
        CREATE TRIGGER larder_notify_products
        AFTER INSERT OR UPDATE OR DELETE ON products
        FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS larder_notify_products ON products")
    op.execute(
        """
        CREATE TRIGGER larder_notify_products
        AFTER INSERT OR UPDATE ON products
        FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
        """
    )
    op.drop_column("products", "archived_at")
