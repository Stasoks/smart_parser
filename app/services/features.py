from __future__ import annotations

import re

from app.schemas import ConditionFeatures, DeviceFeatures, ListingCard, ListingDetails


BRANDS = {
    "lenovo": "Lenovo", "thinkpad": "Lenovo", "apple": "Apple", "iphone": "Apple", "ipad": "Apple",
    "samsung": "Samsung", "xiaomi": "Xiaomi", "redmi": "Xiaomi", "poco": "POCO", "honor": "Honor",
    "huawei": "Huawei", "asus": "ASUS", "acer": "Acer", "dell": "Dell", "hp": "HP", "msi": "MSI",
    "sony": "Sony", "canon": "Canon", "nikon": "Nikon", "fujifilm": "Fujifilm", "realme": "realme",
}

BROKEN_WORDS = (
    "не включается", "не работает", "на запчасти", "разбит", "трещин", "залит", "после воды",
    "нет матрицы", "без матрицы", "без экрана", "неисправ", "под восстановление",
)
FAIR_WORDS = ("царапин", "потерт", "скол", "дефект", "нюанс", "менялся экран", "замена экрана")
EXCELLENT_WORDS = ("идеальное состояние", "как новый", "как новая", "без царапин", "не пользовались")


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
