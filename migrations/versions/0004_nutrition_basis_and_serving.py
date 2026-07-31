"""Add nutrition basis and serving metadata.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("product_nutrition")}
    additions = {
        "basis_unit": sa.Column("basis_unit", sa.Text(), server_default="g", nullable=False),
        "serving_size_text": sa.Column("serving_size_text", sa.Text(), nullable=True),
        "serving_amount": sa.Column("serving_amount", sa.Numeric(10, 3), nullable=True),
        "serving_unit": sa.Column("serving_unit", sa.Text(), nullable=True),
    }
    for name, column in additions.items():
        if name not in columns:
            op.add_column("product_nutrition", column)

    inspector = sa.inspect(bind)
    constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("product_nutrition")
    }
    checks = {
        "nutrition_basis_unit_supported": "basis_unit IN ('g', 'ml')",
        "nutrition_serving_amount_positive": "serving_amount IS NULL OR serving_amount > 0",
        "nutrition_serving_unit_supported": ("serving_unit IS NULL OR serving_unit IN ('g', 'ml')"),
    }
    for name, condition in checks.items():
        convention_name = f"ck_product_nutrition_{name}"
        if name not in constraints and convention_name not in constraints:
            op.create_check_constraint(name, "product_nutrition", condition)


def downgrade() -> None:
    op.drop_constraint("nutrition_serving_unit_supported", "product_nutrition", type_="check")
    op.drop_constraint("nutrition_serving_amount_positive", "product_nutrition", type_="check")
    op.drop_constraint("nutrition_basis_unit_supported", "product_nutrition", type_="check")
    op.drop_column("product_nutrition", "serving_unit")
    op.drop_column("product_nutrition", "serving_amount")
    op.drop_column("product_nutrition", "serving_size_text")
    op.drop_column("product_nutrition", "basis_unit")
