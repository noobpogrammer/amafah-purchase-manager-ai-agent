import pytest
from pydantic import ValidationError

import main


def base_payload():
    return {
        "product_name": "Flexible Wire",
        "specs": "100% copper",
        "deadline_hours": 24,
    }


def test_rfq_accepts_multiple_categories_and_deduplicates():
    req = main.RFQCreateRequest(
        **base_payload(),
        categories=["Electrical", "Hardware", "Electrical"],
        targeting_mode="category",
    )

    assert req.categories == ["Electrical", "Hardware"]
    assert req.category == "Electrical"
    assert req.targeting_mode == "category"


def test_selected_supplier_mode_accepts_one_or_many_suppliers():
    one = main.RFQCreateRequest(
        **base_payload(),
        categories=["Electrical"],
        targeting_mode="selected",
        supplier_ids=["supplier-1"],
    )
    many = main.RFQCreateRequest(
        **base_payload(),
        categories=["Electrical"],
        targeting_mode="selected",
        supplier_ids=["supplier-1", "supplier-2"],
    )

    assert one.supplier_ids == ["supplier-1"]
    assert many.supplier_ids == ["supplier-1", "supplier-2"]


def test_selected_supplier_mode_requires_supplier():
    with pytest.raises(ValidationError):
        main.RFQCreateRequest(
            **base_payload(),
            categories=["Electrical"],
            targeting_mode="selected",
            supplier_ids=[],
        )


def test_rfq_requires_at_least_one_category():
    with pytest.raises(ValidationError):
        main.RFQCreateRequest(
            **base_payload(),
            categories=[],
            category=None,
        )
