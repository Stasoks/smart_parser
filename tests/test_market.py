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
