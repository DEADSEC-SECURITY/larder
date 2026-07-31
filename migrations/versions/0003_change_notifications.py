"""Change notifications for the web UI.

Statement-level triggers post the table name on the `larder_changes` channel so
the API can push live updates to browsers regardless of which process wrote
(scanner daemon, enrichment worker, or web API).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# Tables whose writes should surface in the UI without a reload. inventory_events
# is INSERT-only by design (its guard trigger rejects UPDATE/DELETE).
WATCHED_TABLES = {
    "scan_events": "INSERT OR UPDATE",
    "inventory_items": "INSERT OR UPDATE",
    "inventory_events": "INSERT",
    "products": "INSERT OR UPDATE",
    "grocery_list_items": "INSERT OR UPDATE OR DELETE",
    "product_store_observations": "INSERT",
    "product_stock_policies": "INSERT OR UPDATE OR DELETE",
    "scanner_contexts": "INSERT OR UPDATE",
    "stores": "INSERT OR UPDATE",
    "food_categories": "INSERT OR UPDATE",
    "household_settings": "INSERT OR UPDATE",
}


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION larder_notify_change() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_notify('larder_changes', TG_TABLE_NAME);
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table, events in WATCHED_TABLES.items():
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER larder_notify_{table}
            AFTER {events} ON {table}
            FOR EACH STATEMENT EXECUTE FUNCTION larder_notify_change()
            """
        )


def downgrade() -> None:
    for table in WATCHED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS larder_notify_{table} ON {table}")
    op.execute("DROP FUNCTION IF EXISTS larder_notify_change()")
