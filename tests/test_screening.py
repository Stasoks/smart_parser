from app.schemas import ListingCard
from app.services.features import screen_listing


def item(title: str, price: int, snippet: str = "") -> ListingCard:
    return ListingCard(
        external_id="1",
        title=title,
        price=price,
        url="https://example.com/1",
        snippet=snippet,
    )


def test_rejects_parts_listing():
    decision = screen_listing(item("Lenovo ThinkPad T480 на запчасти", 5000))
    assert decision.rejected
    assert "запчасти" in decision.reason


def test_rejects_bulk_catalogue():
    decision = screen_listing(
        item("Ноутбуки Lenovo в наличии", 5000, "Большой выбор, разные модели и цены")
    )
    assert decision.rejected


def test_rejects_teaser_price_with_real_higher_prices():
    decision = screen_listing(
        item(
            "Ноутбуки ThinkPad",
            5000,
            "ThinkPad T480 15 000 ₽, T470 12 000 ₽, X270 10 000 ₽",
        )
    )
    assert decision.rejected


def test_keeps_normal_single_device():
    decision = screen_listing(
        item(
            "Lenovo ThinkPad T480 i5-8350U 16GB 256GB SSD",
            11000,
            "Один ноутбук, рабочий, состояние хорошее",
        )
    )
    assert not decision.rejected
