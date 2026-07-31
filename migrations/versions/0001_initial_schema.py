"""Initial Larder schema.

Revision ID: 0001
Revises: None
"""

from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

from larder.models import Base
from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    InventoryStatus,
    ScanKind,
    ScannerMode,
    ScanResult,
)

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


ENUMS = (
    ENUM(ScannerMode, name="scanner_mode"),
    ENUM(ScanKind, name="scan_kind"),
    ENUM(ScanResult, name="scan_result"),
    ENUM(InventoryStatus, name="inventory_status"),
    ENUM(InventoryEventType, name="inventory_event_type"),
    ENUM(EnrichmentStatus, name="enrichment_status"),
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in ENUMS:
        enum.create(bind, checkfirst=True)
    Base.metadata.create_all(bind=bind, checkfirst=True)

    op.execute(
        """
        INSERT INTO food_categories
            (id, display_name, expiration_basis, active, created_at, updated_at)
        VALUES
            ('local:uncategorized', 'Uncategorized', 'DATE_ADDED', true, now(), now())
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO control_barcodes (payload, command_type, mode, enabled, description)
        VALUES
            ('CMD:MODE:ADD', 'SET_MODE', 'ADD', true, 'Add inventory'),
            ('CMD:MODE:REMOVE', 'SET_MODE', 'REMOVE', true, 'Remove inventory'),
            ('CMD:MODE:TRASH', 'SET_MODE', 'TRASH', true, 'Trash inventory'),
            ('CMD:MODE:CLEAR', 'CLEAR_MODE', NULL, true, 'Clear operation mode'),
            ('CMD:STORE:CLEAR', 'CLEAR_STORE', NULL, true, 'Clear store context')
        ON CONFLICT (payload) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_inventory_event_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'inventory_events is append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER inventory_events_append_only
        BEFORE UPDATE OR DELETE ON inventory_events
        FOR EACH ROW EXECUTE FUNCTION reject_inventory_event_mutation();
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind, checkfirst=True)
    op.execute("DROP FUNCTION IF EXISTS reject_inventory_event_mutation()")
    for enum in reversed(ENUMS):
        enum.drop(bind, checkfirst=True)
