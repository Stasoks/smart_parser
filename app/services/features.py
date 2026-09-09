from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import ConditionFeatures, DeviceFeatures, ListingCard, ListingDetails


BRANDS = {
    "lenovo": "Lenovo", "thinkpad": "Lenovo", "apple": "Apple", "iphone": "Apple", "ipad": "Apple",
    "samsung": "Samsung", "xiaomi": "Xiaomi", "redmi": "Xiaomi", "poco": "POCO", "honor": "Honor",
    "huawei": "Huawei", "asus": "ASUS", "acer": "Acer", "dell": "Dell", "hp": "HP", "msi": "MSI",
    "sony": "Sony", "canon": "Canon", "nikon": "Nikon", "fujifilm": "Fujifilm", "realme": "realme",
}

BROKEN_WORDS = (
    "не включается", "не работает", "на запчасти", "запчасти", "донор", "разбор",
    "разбит", "трещин", "залит", "после воды", "после залития", "нет матрицы",
    "без матрицы", "без экрана", "неисправ", "под восстановление",
)
FAIR_WORDS = ("царапин", "потерт", "скол", "дефект", "нюанс", "менялся экран", "замена экрана")
EXCELLENT_WORDS = ("идеальное состояние", "как новый", "как новая", "без царапин", "не пользовались")

PARTS_PATTERNS = (
    r"\bна\s+запчаст",
    r"\bзапчаст[ьи]",
    r"\bдонор\b",
    r"\bразбор\b",
    r"\bпод\s+восстанов",
    r"\bне\s+включает",
    r"\bнеисправ",
    r"\bпосле\s+(?:воды|залит)",
    r"\bбез\s+(?:матрицы|экрана|платы)",
)
ACCESSORY_PATTERNS = (
    r"\bматрица\s+(?:для|на)\b",
    r"\bклавиатура\s+(?:для|на)\b",
    r"\bкорпус\s+(?:для|на)\b",
    r"\bаккумулятор\s+(?:для|на)\b",
    r"\bбатарея\s+(?:для|на)\b",
    r"\bзарядк[аи]\s+(?:для|на)\b",
    r"\bдисплей\s+(?:для|на)\b",
    r"\bматеринская\s+плата\b",
)
BULK_PATTERNS = (
    r"\bкуча\s+(?:ноут|телефон|планшет)",
    r"\bмного\s+(?:ноут|телефон|планшет)",
    r"\bноутбуки\s+(?:в\s+наличии|оптом|разные)",
    r"\bтелефоны\s+(?:в\s+наличии|оптом|разные)",
    r"\bпланшеты\s+(?:в\s+наличии|оптом|разные)",
    r"\bразные\s+(?:модели|ноутбуки|телефоны|планшеты)",
    r"\bбольшой\s+выбор\b",
    r"\bассортимент\b",
    r"\bоптом\b",
)
PRICE_TRAP_PATTERNS = (
    r"\bцена\s+от\b",
    r"\bцены\s+от\b",
    r"\bстоимость\s+от\b",
    r"\bот\s+\d[\d\s]{2,}\s*(?:₽|р\.?|руб)",
    r"\bцена\s+(?:указана|стоит)\s+(?:за|для)\b",
    r"\bцена\s+в\s+объявлении\b",
    r"\bактуальн\w*\s+цен\w*\s+уточ",
    r"\bцен\w*\s+уточня",
    r"\bразные\s+цены\b",
)


@dataclass(frozen=True, slots=True)
class ListingScreen:
    rejected: bool
    reason: str = ""


def screen_listing(listing: ListingCard | ListingDetails) -> ListingScreen:
    """Reject obvious parts, bulk catalogues and misleading teaser-price listings."""
    if listing.price is None or listing.price <= 0:
        return ListingScreen(True, "нет нормальной цены")

    text = f"{listing.title} {getattr(listing, 'snippet', '')} {getattr(listing, 'description', '')}".lower()
    text = re.sub(r"\s+", " ", text)

    for pattern in PARTS_PATTERNS:
        if re.search(pattern, text, re.I):
            return ListingScreen(True, "запчасти/неисправное устройство")

    for pattern in ACCESSORY_PATTERNS:
        if re.search(pattern, text, re.I):
            return ListingScreen(True, "комплектующая, а не устройство")

    for pattern in BULK_PATTERNS:
        if re.search(pattern, text, re.I):
            return ListingScreen(True, "опт/несколько устройств")

    for pattern in PRICE_TRAP_PATTERNS:
        if re.search(pattern, text, re.I):
            return ListingScreen(True, "цена-приманка или цена «от»")

    currency_prices = _currency_prices(text)
    if len(currency_prices) >= 3:
        return ListingScreen(True, "в объявлении несколько товаров/цен")

    if currency_prices:
        higher = [value for value in currency_prices if value >= listing.price * 1.45 and value - listing.price >= 2500]
        if higher:
            return ListingScreen(True, "в тексте указана существенно более высокая реальная цена")

    return ListingScreen(False)


def local_extract_device(listing: ListingCard | ListingDetails, category: str = "other") -> DeviceFeatures:
    text = f"{listing.title} {getattr(listing, 'params_text', '')} {getattr(listing, 'description', '')}".lower()
    brand = ""
    for token, canonical in BRANDS.items():
        if token in text:
            brand = canonical
            break

    ram = _number_before(text, r"(?:gb|гб)\s*(?:ram|озу|оператив|памят[ьи])")
    if ram is None:
        ram = _number_after(text, r"(?:ram|озу|оператив\w*)\s*[:\-]?\s*")
    storage = _number_before(text, r"(?:gb|гб|tb|тб)\s*(?:ssd|hdd|накоп|памят)")
    storage_type = "SSD" if "ssd" in text else ("HDD" if "hdd" in text else "")

    cpu = ""
    for pattern in (
        r"\b(i[3579]-?\d{4,5}[a-z]{0,2})\b",
        r"\b(ryzen\s*[3579]\s*\d{4}[a-z]{0,2})\b",
        r"\b(m[1-5](?:\s*(?:pro|max|ultra))?)\b",
        r"\b(snapdragon\s*[\w+\- ]{2,16})\b",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            cpu = re.sub(r"\s+", " ", match.group(1)).upper().replace("RYZEN", "Ryzen")
            break

    model = _guess_model(listing.title, brand)
    canonical = " ".join(part for part in (brand, model) if part).strip() or listing.title[:80]
    return DeviceFeatures(
        category=category,
        brand=brand,
        model=model,
        canonical_name=canonical,
        cpu=cpu,
        ram_gb=ram,
        storage_gb=storage,
        storage_type=storage_type,
    )


def local_extract_condition(listing: ListingCard | ListingDetails) -> ConditionFeatures:
    text = f"{listing.title} {getattr(listing, 'description', '')} {getattr(listing, 'params_text', '')}".lower()
    defects = [word for word in BROKEN_WORDS + FAIR_WORDS if word in text]
    if any(word in text for word in BROKEN_WORDS):
        state, risk = "broken", 0.95
    elif any(word in text for word in FAIR_WORDS):
        state, risk = "fair", 0.62
    elif any(word in text for word in EXCELLENT_WORDS):
        state, risk = "excellent", 0.15
    else:
        state, risk = "unknown", 0.35
    return ConditionFeatures(state=state, defects=defects[:8], repair_risk=risk)


def identity_key(features: DeviceFeatures) -> str:
    canonical = (features.canonical_name or f"{features.brand} {features.model}").lower().strip()
    canonical = re.sub(r"[^a-zа-я0-9]+", " ", canonical)
    return re.sub(r"\s+", " ", canonical).strip()


def _currency_prices(text: str) -> list[int]:
    values: list[int] = []
    patterns = (
        r"(?<!\d)(\d[\d\s]{2,7})\s*(?:₽|руб(?:\.|лей)?|р\.?)",
        r"(?:цена|стоимость|стоит|за)\s*[:=\-]?\s*(\d[\d\s]{2,7})(?!\w)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                value = int(digits)
                if 500 <= value <= 10_000_000:
                    values.append(value)
    return list(dict.fromkeys(values))


def _number_before(text: str, suffix_pattern: str) -> float | None:
    match = re.search(rf"\b(\d{{1,4}})\s*{suffix_pattern}", text, re.I)
    if not match:
        return None
    value = float(match.group(1))
    if re.search(r"\b" + re.escape(match.group(1)) + r"\s*(?:tb|тб)", match.group(0), re.I):
        value *= 1024
    return value


def _number_after(text: str, prefix_pattern: str) -> float | None:
    match = re.search(rf"{prefix_pattern}(\d{{1,4}})\s*(?:gb|гб)?", text, re.I)
    return float(match.group(1)) if match else None


def _guess_model(title: str, brand: str) -> str:
    clean = re.sub(r"\([^)]*\)", " ", title)
    clean = re.sub(r"\b\d+\s*(?:gb|гб|tb|тб)\b", " ", clean, flags=re.I)
    clean = re.sub(r"\b(?:ssd|hdd|ram|озу)\b", " ", clean, flags=re.I)
    clean = re.sub(
        r"\b(?:i[3579]-?\d{4,5}[a-z]{0,2}|ryzen\s*[3579]\s*\d{4}[a-z]{0,2})\b",
        " ",
        clean,
        flags=re.I,
    )
    if brand:
        clean = re.sub(re.escape(brand), " ", clean, flags=re.I)
        if brand == "Lenovo":
            clean = re.sub(r"\blenovo\b", " ", clean, flags=re.I)
    clean = re.sub(r"[^\w\-+ ]+", " ", clean)
    words = [w for w in clean.split() if len(w) > 1]
    return " ".join(words[:5]).strip()
