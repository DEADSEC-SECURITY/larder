import unittest
from decimal import Decimal

from larder.enrichment.mapper import map_nutrition, map_product


class NutritionMapperTests(unittest.TestCase):
    def test_missing_values_remain_null_and_strings_map(self):
        mapped = map_nutrition({"fat_100g": "12.5", "sugars_100g": None})
        self.assertEqual(mapped.values["fat_g_100g"], Decimal("12.5"))
        self.assertIsNone(mapped.values["sugars_g_100g"])
        self.assertIsNone(mapped.values["protein_g_100g"])

    def test_invalid_negative_nan_and_infinity_are_null(self):
        mapped = map_nutrition(
            {
                "fat_100g": -1,
                "sugars_100g": "NaN",
                "proteins_100g": "Infinity",
                "salt_100g": "not-a-number",
            }
        )
        self.assertIsNone(mapped.values["fat_g_100g"])
        self.assertIsNone(mapped.values["sugars_g_100g"])
        self.assertIsNone(mapped.values["protein_g_100g"])
        self.assertIsNone(mapped.values["salt_g_100g"])
        self.assertEqual(len(mapped.quality_notes), 4)

    def test_outlier_is_retained_and_noted(self):
        mapped = map_nutrition({"fat_100g": "101"})
        self.assertEqual(mapped.values["fat_g_100g"], Decimal("101"))
        self.assertIn("nutrition_outlier:fat_100g", mapped.quality_notes)

    def test_v36_aggregated_nutrition_maps_per_100g_values(self):
        mapped = map_nutrition(
            {
                "aggregated_set": {
                    "per": "100g",
                    "preparation": "as_sold",
                    "nutrients": {
                        "energy-kcal": {"value": 539, "unit": "kcal"},
                        "fat": {"value": 30.9, "unit": "g"},
                        "sodium": {"value": 0.043, "unit": "g"},
                    },
                }
            }
        )
        self.assertEqual(mapped.values["energy_kcal_100g"], Decimal("539"))
        self.assertEqual(mapped.values["fat_g_100g"], Decimal("30.9"))
        self.assertEqual(mapped.values["sodium_g_100g"], Decimal("0.043"))
        self.assertEqual(mapped.basis_unit, "g")

    def test_v36_beverage_nutrition_maps_per_100ml_and_serving(self):
        mapped = map_nutrition(
            {
                "aggregated_set": {
                    "per": "100ml",
                    "preparation": "as_sold",
                    "nutrients": {
                        "energy-kcal": {"value": 23.5294, "unit": "kcal"},
                        "carbohydrates": {"value": 5.5882, "unit": "g"},
                        "sugars": {"value": 5.2941, "unit": "g"},
                    },
                }
            },
            "340 ml (1 CAN)",
        )
        self.assertEqual(mapped.values["energy_kcal_100g"], Decimal("23.5294"))
        self.assertEqual(mapped.basis_unit, "ml")
        self.assertEqual(mapped.serving_size_text, "340 ml (1 CAN)")
        self.assertEqual(mapped.serving_amount, Decimal("340"))
        self.assertEqual(mapped.serving_unit, "ml")

    def test_serving_units_are_normalized_to_nutrition_basis_units(self):
        mapped = map_nutrition({}, "0.34 l")
        self.assertEqual(mapped.serving_amount, Decimal("340.00"))
        self.assertEqual(mapped.serving_unit, "ml")

    def test_v36_wrong_units_are_rejected(self):
        mapped = map_nutrition(
            {
                "aggregated_set": {
                    "per": "100g",
                    "preparation": "as_sold",
                    "nutrients": {"fat": {"value": 30, "unit": "mg"}},
                }
            }
        )
        self.assertIsNone(mapped.values["fat_g_100g"])
        self.assertIn("invalid_nutrition_unit:fat:mg", mapped.quality_notes)

    def test_product_mapping_deduplicates_category_hierarchy(self):
        mapped = map_product(
            {
                "product_name": "  Plain   Yogurt ",
                "categories_hierarchy": ["en:dairies", "en:yogurts", "en:yogurts"],
                "nutriments": {},
            }
        )
        self.assertEqual(mapped.values["name"], "Plain Yogurt")
        self.assertEqual(mapped.categories, ["en:dairies", "en:yogurts"])
        self.assertEqual(
            mapped.category_parents,
            {"en:dairies": None, "en:yogurts": "en:dairies"},
        )

    def test_product_mapping_prefers_official_taxonomy_parents(self):
        mapped = map_product(
            {
                "categories_tags": ["en:fruits", "en:berries", "en:strawberries"],
                "_category_parents": {
                    "en:fruits": "en:fruit-based-foods",
                    "en:berries": "en:fruits",
                    "en:strawberries": "en:berries",
                },
                "nutriments": {},
            }
        )
        self.assertEqual(
            mapped.category_parents["en:strawberries"], "en:berries"
        )

    def test_product_mapping_rejects_noncanonical_category_ids(self):
        mapped = map_product(
            {
                "categories_tags": [
                    "en:protein-shakes",
                    "en:Batidos de proteínas",
                ],
                "nutriments": {},
            }
        )
        self.assertEqual(mapped.categories, ["en:protein-shakes"])
        self.assertIn("invalid:category_id", mapped.quality_notes)

    def test_product_and_brand_names_are_standardized(self):
        mapped = map_product(
            {
                "product_name": "HALF & half ICED tea LEMONADE",
                "brands": "ARNOLD pALMER LITE",
            }
        )
        self.assertEqual(mapped.values["name"], "Half & Half Iced Tea Lemonade")
        self.assertEqual(mapped.values["brand"], "Arnold Palmer Lite")

    def test_v36_selected_front_image_is_supported(self):
        mapped = map_product(
            {
                "selected_images": {
                    "front": {
                        "display": {"en": "https://images.openfoodfacts.org/front.jpg"}
                    }
                }
            }
        )
        self.assertEqual(
            mapped.values["image_url"], "https://images.openfoodfacts.org/front.jpg"
        )

    def test_product_mapping_passes_serving_size_to_nutrition(self):
        mapped = map_product(
            {
                "serving_size": "85 g (1 bar)",
                "nutrition": {
                    "aggregated_set": {
                        "per": "100g",
                        "preparation": "as_sold",
                        "nutrients": {},
                    }
                },
            }
        )
        self.assertEqual(mapped.nutrition.serving_amount, Decimal("85"))
        self.assertEqual(mapped.nutrition.serving_unit, "g")


if __name__ == "__main__":
    unittest.main()
