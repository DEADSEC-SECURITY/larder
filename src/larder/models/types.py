from sqlalchemy.dialects.postgresql import ENUM

from larder.models.enums import (
    EnrichmentStatus,
    InventoryEventType,
    InventoryStatus,
    ScanKind,
    ScannerMode,
    ScanResult,
)

scanner_mode_type = ENUM(ScannerMode, name="scanner_mode", create_type=False)
scan_kind_type = ENUM(ScanKind, name="scan_kind", create_type=False)
scan_result_type = ENUM(ScanResult, name="scan_result", create_type=False)
inventory_status_type = ENUM(InventoryStatus, name="inventory_status", create_type=False)
inventory_event_type = ENUM(InventoryEventType, name="inventory_event_type", create_type=False)
enrichment_status_type = ENUM(EnrichmentStatus, name="enrichment_status", create_type=False)
