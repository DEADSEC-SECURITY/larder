from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from larder.domain.stock import automatic_required_quantity
from larder.models import (
    GroceryListItem,
    InventoryItem,
    Product,
    ProductStockPolicy,
    StockGroup,
    StockGroupPolicy,
    StockGroupProduct,
    StockGroupShoppingItem,
)
from larder.models.enums import InventoryStatus


async def recalculate_stock_policy(session: AsyncSession, product_barcode: str) -> int:
    # This lock serializes count-and-upsert projection work across scanners.
    await session.scalar(
        select(Product.barcode).where(Product.barcode == product_barcode).with_for_update()
    )
    policy = await session.get(ProductStockPolicy, product_barcode, with_for_update=True)
    list_item = await session.get(GroceryListItem, product_barcode, with_for_update=True)
    if policy is None:
        automatic_quantity = 0
    else:
        on_hand = await session.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .where(
                InventoryItem.product_barcode == product_barcode,
                InventoryItem.status == InventoryStatus.IN_STOCK,
            )
        )
        automatic_quantity = automatic_required_quantity(
            on_hand=on_hand or 0,
            reorder_point=policy.reorder_point,
            target_stock=policy.target_stock,
            enabled=policy.enabled,
        )

    if list_item is None and automatic_quantity:
        session.add(
            GroceryListItem(
                product_barcode=product_barcode,
                automatic_quantity=automatic_quantity,
                auto_reason="BELOW_REORDER_POINT",
            )
        )
    elif list_item is not None:
        # A row the shopper deleted stays hidden until demand genuinely
        # returns: stock has to climb back above the reorder point (clearing
        # the automatic quantity) before a fresh shortfall can re-list it.
        if list_item.dismissed_at is not None and list_item.automatic_quantity == 0:
            list_item.dismissed_at = None
        list_item.automatic_quantity = automatic_quantity
        list_item.auto_reason = "BELOW_REORDER_POINT" if automatic_quantity else None
        if (
            automatic_quantity == 0
            and list_item.manual_quantity == 0
            and list_item.checked_quantity == 0
            and list_item.preferred_store_id is None
        ):
            await session.delete(list_item)
    membership = await session.scalar(
        select(StockGroupProduct.stock_group_id).where(
            StockGroupProduct.product_barcode == product_barcode
        )
    )
    if membership is not None:
        await recalculate_stock_group_policy(session, membership)
    return automatic_quantity


async def recalculate_stock_group_policy(
    session: AsyncSession, stock_group_id: UUID
) -> int:
    group = await session.scalar(
        select(StockGroup)
        .where(StockGroup.id == stock_group_id)
        .with_for_update()
    )
    if group is None:
        return 0
    policy = await session.get(StockGroupPolicy, stock_group_id, with_for_update=True)
    list_item = await session.get(
        StockGroupShoppingItem, stock_group_id, with_for_update=True
    )
    if policy is None or not group.active:
        automatic_quantity = 0
    else:
        on_hand = await session.scalar(
            select(func.count())
            .select_from(InventoryItem)
            .join(
                StockGroupProduct,
                StockGroupProduct.product_barcode == InventoryItem.product_barcode,
            )
            .where(
                StockGroupProduct.stock_group_id == stock_group_id,
                InventoryItem.status == InventoryStatus.IN_STOCK,
            )
        )
        automatic_quantity = automatic_required_quantity(
            on_hand=on_hand or 0,
            reorder_point=policy.reorder_point,
            target_stock=policy.target_stock,
            enabled=policy.enabled,
        )

    if list_item is None and automatic_quantity:
        session.add(
            StockGroupShoppingItem(
                stock_group_id=stock_group_id,
                automatic_quantity=automatic_quantity,
                manual_quantity=0,
                checked_quantity=0,
                auto_reason="BELOW_REORDER_POINT",
            )
        )
    elif list_item is not None:
        # A row the shopper deleted stays hidden until demand genuinely
        # returns: stock has to climb back above the reorder point (clearing
        # the automatic quantity) before a fresh shortfall can re-list it.
        if list_item.dismissed_at is not None and list_item.automatic_quantity == 0:
            list_item.dismissed_at = None
        list_item.automatic_quantity = automatic_quantity
        list_item.auto_reason = "BELOW_REORDER_POINT" if automatic_quantity else None
        if (
            automatic_quantity == 0
            and list_item.manual_quantity == 0
            and list_item.checked_quantity == 0
            and list_item.preferred_store_id is None
        ):
            await session.delete(list_item)
    return automatic_quantity
