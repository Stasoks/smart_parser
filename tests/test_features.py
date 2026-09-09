from app.schemas import ListingDetails
from app.services.features import (
    identity_key,
    local_extract_condition,
    local_extract_device,
)


def test_local_feature_extraction():
    item = ListingDetails(
        external_id="1",
        title="Lenovo ThinkPad T480 i5-8350U 16GB 256GB SSD",
        price=12000,
        url="https://example.com",
        description="Работает отлично, без серьёзных дефектов",
    )
    features = local_extract_device(item, "laptops")
    assert features.brand == "Lenovo"
    assert "T480" in features.model.upper()
    assert features.cpu
    assert identity_key(features)


def test_broken_condition_penalty_signal():
    item = ListingDetails(
        external_id="2",
        title="Ноутбук на запчасти",
        price=2000,
        url="https://example.com/2",
        description="Не включается после воды",
    )
    condition = local_extract_condition(item)
    assert condition.state == "broken"
    assert condition.repair_risk >= 0.9
