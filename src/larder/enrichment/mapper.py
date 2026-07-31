from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from larder.domain.names import standardize_name
from larder.schemas.enrichment import NutritionMapping, ProductMapping

CATEGORY_ID_PATTERN = re.compile(r"^[a-z0-9_]+:[a-z0-9]+(?:-[a-z0-9]+)*$")

PRODUCT_KINDS = {"FOOD", "HOUSEHOLD", "PERSONAL_CARE", "MEDICAL", "PET", "OFFICE", "OTHER"}


def classify_product_kind(product: dict) -> str:
    declared = str(product.get("_product_type") or product.get("product_type") or "").casefold()
    if "beauty" in declared:
        return "PERSONAL_CARE"
    if "pet" in declared:
        return "PET"
    if "food" in declared:
        return "FOOD"
    haystack = " ".join(
        str(product.get(key) or "")
        for key in ("categories", "product_name", "_description")
    ).casefold()
    rules = (
        ("PET", ("pet ", "dog ", "cat ", "animal")),
        ("OFFICE", ("office", "stationery", "printer", "paper product")),
        ("MEDICAL", ("medical", "first aid", "rubbing alcohol", "bandage", "pharmacy")),
        ("PERSONAL_CARE", ("personal care", "beauty", "cosmetic", "skin care", "hair care")),
        ("HOUSEHOLD", ("household", "home", "cleaning", "laundry", "kitchen", "glove")),
        ("FOOD", ("grocery", "food", "beverage", "drink", "snack")),
    )
    for kind, needles in rules:
        if any(needle in haystack for needle in needles):
            return kind
    return "OTHER"

NUTRITION_FIELDS = {
    "energy_kcal_100g": ("energy-kcal_100g", "energy-kcal", "kcal"),
    "fat_g_100g": ("fat_100g", "fat", "g"),
    "saturated_fat_g_100g": ("saturated-fat_100g", "saturated-fat", "g"),
    "carbohydrates_g_100g": ("carbohydrates_100g", "carbohydrates", "g"),
    "sugars_g_100g": ("sugars_100g", "sugars", "g"),
    "fiber_g_100g": ("fiber_100g", "fiber", "g"),
    "protein_g_100g": ("proteins_100g", "proteins", "g"),
    "salt_g_100g": ("salt_100g", "salt", "g"),
    "sodium_g_100g": ("sodium_100g", "sodium", "g"),
}


def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _standardized_name(value) -> str | None:
    cleaned = _clean_text(value)
    return standardize_name(cleaned) if cleaned else None


def _timestamp(value) -> datetime | None:
    try:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            return None
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _selected_front_image(product: dict) -> str | None:
    selected = product.get("selected_images")
    if not isinstance(selected, dict):
        return None
    front = selected.get("front")
    if not isinstance(front, dict):
        return None
    for size in ("display", "small", "thumb"):
        localized = front.get(size)
        if not isinstance(localized, dict):
            continue
        for value in localized.values():
            cleaned = _clean_text(value)
            if cleaned is not None:
                return cleaned
    return None


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _v36_nutrients(nutrition) -> tuple[dict, str] | None:
    if not isinstance(nutrition, dict):
        return None
    aggregated = nutrition.get("aggregated_set")
    if not isinstance(aggregated, dict):
        return None
    per = str(aggregated.get("per", "")).lower().replace(" ", "")
    basis_unit = {"100g": "g", "100ml": "ml"}.get(per)
    if basis_unit is None or aggregated.get("preparation") != "as_sold":
        return None
    nutrients = aggregated.get("nutrients")
    return (nutrients, basis_unit) if isinstance(nutrients, dict) else None


def _serving_measure(value) -> tuple[str | None, Decimal | None, str | None]:
    text = _clean_text(value)
    if text is None:
        return None, None, None
    match = re.match(r"^(\d+(?:[.,]\d+)?)\s*(ml|cl|l|g|kg)\b", text, re.IGNORECASE)
    if match is None:
        return text, None, None
    amount = _decimal(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if amount is None or amount == 0:
        return text, None, None
    if unit == "cl":
        amount *= Decimal("10")
        unit = "ml"
    elif unit == "l":
        amount *= Decimal("1000")
        unit = "ml"
    elif unit == "kg":
        amount *= Decimal("1000")
        unit = "g"
    return text, amount, unit


def map_nutrition(nutrition, serving_size=None) -> NutritionMapping:
    source = nutrition if isinstance(nutrition, dict) else {}
    v36 = _v36_nutrients(nutrition)
    v36_nutrients = v36[0] if v36 is not None else None
    basis_unit = v36[1] if v36 is not None else "g"
    serving_size_text, serving_amount, serving_unit = _serving_measure(serving_size)
    values: dict[str, Decimal | None] = {}
    notes: list[str] = []
    for target, (legacy_field, nutrient_id, expected_unit) in NUTRITION_FIELDS.items():
        if v36_nutrients is None:
            raw = source.get(legacy_field)
            field_label = legacy_field
        else:
            nutrient = v36_nutrients.get(nutrient_id)
            nutrient = nutrient if isinstance(nutrient, dict) else {}
            raw = nutrient.get("value")
            field_label = nutrient_id
            unit = nutrient.get("unit")
            if raw is not None and unit != expected_unit:
                notes.append(f"invalid_nutrition_unit:{nutrient_id}:{unit}")
                raw = None
        value = _decimal(raw)
        values[target] = value
        if raw is not None and value is None:
            notes.append(f"invalid_nutrition:{field_label}")
        if value is not None:
            limit = Decimal("1000") if target == "energy_kcal_100g" else Decimal("100")
            if value > limit:
                notes.append(f"nutrition_outlier:{field_label}")
    return NutritionMapping(
        values=values,
        basis_unit=basis_unit,
        serving_size_text=serving_size_text,
        serving_amount=serving_amount,
        serving_unit=serving_unit,
        quality_notes=notes,
    )


def map_product(product: dict) -> ProductMapping:
    raw_hierarchy = product.get("categories_hierarchy")
    has_hierarchy = isinstance(raw_hierarchy, list)
    source_categories = (
        raw_hierarchy if has_hierarchy else product.get("categories_tags")
    )
    raw_categories = [
        item for item in source_categories or [] if isinstance(item, str) and item
    ]
    categories = [
        category_id
        for category_id in raw_categories
        if CATEGORY_ID_PATTERN.fullmatch(category_id)
    ]
    categories = list(dict.fromkeys(categories))
    category_parents: dict[str, str | None] = {}
    category_labels = {
        key: standardize_name(value)
        for key, value in (product.get("_category_labels") or {}).items()
        if key in categories and isinstance(value, str) and value.strip()
    }
    official_parents = product.get("_category_parents")
    if isinstance(official_parents, dict):
        for category_id in categories:
            if category_id not in official_parents:
                continue
            parent_id = official_parents[category_id]
            category_parents[category_id] = (
                parent_id if isinstance(parent_id, str) and parent_id else None
            )
    elif has_hierarchy:
        for index, category_id in enumerate(categories):
            category_parents[category_id] = categories[index - 1] if index else None
    nutrition_source = product.get("nutrition") or product.get("nutriments")
    nutrition = map_nutrition(nutrition_source, product.get("serving_size"))
    image_url = _clean_text(product.get("image_front_url")) or _selected_front_image(
        product
    )
    values = {
        "name": _standardized_name(product.get("product_name")),
        "brand": _standardized_name(product.get("brands")),
        "description": _clean_text(product.get("_description") or product.get("generic_name")),
        "manufacturer": _standardized_name(
            product.get("_manufacturer") or product.get("manufacturing_places")
        ),
        "package_size": _clean_text(product.get("quantity")),
        "material": _standardized_name(product.get("_material")),
        "variant": _standardized_name(product.get("_variant")),
        "product_kind": classify_product_kind(product),
        "ingredients_text": _clean_text(product.get("ingredients_text")),
        "quantity_text": _clean_text(product.get("quantity")),
        "image_url": image_url,
        "image_source": "OPEN_FOOD_FACTS" if image_url else None,
        "off_product_url": _clean_text(product.get("url")),
        "off_last_modified_at": _timestamp(product.get("last_modified_t")),
    }
    notes = list(nutrition.quality_notes)
    if len(categories) != len(raw_categories):
        notes.append("invalid:category_id")
    for field in ("name", "brand", "quantity_text"):
        if values[field] is None:
            notes.append(f"missing:{field}")
    if not categories:
        notes.append("missing:categories")
    return ProductMapping(
        values=values,
        categories=categories,
        category_parents=category_parents,
        category_labels=category_labels,
        nutrition=nutrition,
        quality_notes=notes,
    )


def category_display_name(category_id: str) -> str:
    value = category_id.split(":", 1)[-1].replace("-", " ").replace("_", " ")
    return standardize_name(value)
