from app.schemas import DeviceFeatures, ListingCard
from app.services.market import MarketEstimator


def card(i: int, price: int) -> ListingCard:
    return ListingCard(
        external_id=str(i),
        title="Lenovo ThinkPad T480",
        price=price,
        url=f"https://example.com/{i}",
    )


def test_market_estimator_finds_discount():
    items = [
        card(1, 9000),
        card(2, 14000),
        card(3, 15000),
        card(4, 16000),
        card(5, 15500),
    ]
    features = {
        x.external_id: DeviceFeatures(
            category="laptops",
            brand="Lenovo",
            model="ThinkPad T480",
            canonical_name="Lenovo ThinkPad T480",
        )
        for x in items
    }
    stats = MarketEstimator(minimum_comparables=3).estimate_all(items, features)["1"]
    assert stats.comparable_count >= 3
    assert stats.median_price >= 14500
    assert stats.discount_pct is not None and stats.discount_pct > 0.3
    assert stats.expected_profit is not None and stats.expected_profit > 4000


def test_market_does_not_fallback_to_unrelated_same_brand_models():
    target = card(1, 9000)
    others = [card(2, 50000), card(3, 52000), card(4, 54000)]
    items = [target] + others
    features = {
        "1": DeviceFeatures(category="laptops", brand="Lenovo", model="ThinkPad T480", canonical_name="Lenovo ThinkPad T480"),
        "2": DeviceFeatures(category="laptops", brand="Lenovo", model="ThinkPad T14 Gen 4", canonical_name="Lenovo ThinkPad T14 Gen 4"),
        "3": DeviceFeatures(category="laptops", brand="Lenovo", model="ThinkPad T14 Gen 4", canonical_name="Lenovo ThinkPad T14 Gen 4"),
        "4": DeviceFeatures(category="laptops", brand="Lenovo", model="ThinkPad T14 Gen 4", canonical_name="Lenovo ThinkPad T14 Gen 4"),
    }
    stats = MarketEstimator(minimum_comparables=3).estimate_all(items, features)["1"]
    assert stats.comparable_count < 3
    assert stats.median_price is None
    assert stats.expected_profit is None
