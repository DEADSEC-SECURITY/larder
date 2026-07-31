def automatic_required_quantity(
    *, on_hand: int, reorder_point: int, target_stock: int, enabled: bool = True
) -> int:
    if min(on_hand, reorder_point, target_stock) < 0:
        raise ValueError("stock quantities cannot be negative")
    if target_stock < reorder_point:
        raise ValueError("target stock cannot be below reorder point")
    if not enabled or on_hand >= reorder_point:
        return 0
    return target_stock - on_hand
