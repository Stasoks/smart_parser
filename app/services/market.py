from __future__ import annotations

import math
import re
from difflib import SequenceMatcher

import numpy as np

from app.schemas import DeviceFeatures, ListingCard, MarketStats


GENERIC_TOKENS = {
    "lenovo", "apple", "samsung", "xiaomi", "asus", "acer", "dell", "hp", "msi",
    "ноутбук", "ноут", "телефон", "смартфон", "планшет", "компьютер", "pc",
}


class MarketEstimator:
    def __init__(self, minimum_comparables: int = 3):
        self.minimum_comparables = minimum_comparables

    def estimate_all(
        self,
        listings: list[ListingCard],
        features: dict[str, DeviceFeatures],
    ) -> dict[str, MarketStats]:
        output: dict[str, MarketStats] = {}
        for item in listings:
            target = features[item.external_id]
            candidates = []
            for other in listings:
                if other.external_id == item.external_id or not other.price or other.price <= 0:
                    continue
                candidate = features[other.external_id]
                sim = self._similarity(target, candidate)
                if sim >= 0.62:
                    candidates.append((other, sim))

            candidates.sort(key=lambda x: x[1], reverse=True)
            output[item.external_id] = self._estimate(
                item,
                [x[0] for x in candidates[:24]],
            )
        return output

    def _similarity(self, a: DeviceFeatures, b: DeviceFeatures) -> float:
        if a.category != b.category:
            return 0.0
        if a.brand and b.brand and a.brand.lower() != b.brand.lower():
            return 0.0

        a_name = self._model_text(a)
        b_name = self._model_text(b)
        if not a_name or not b_name:
            return 0.0

        seq = SequenceMatcher(None, a_name, b_name).ratio()
        a_tokens = set(a_name.split())
        b_tokens = set(b_name.split())
        union = a_tokens | b_tokens
        jaccard = len(a_tokens & b_tokens) / len(union) if union else 0.0
        score = 0.45 * seq + 0.45 * jaccard

        if a.cpu and b.cpu:
            score += 0.12 if self._norm(a.cpu) == self._norm(b.cpu) else -0.08
        if a.ram_gb and b.ram_gb:
            ratio = min(a.ram_gb, b.ram_gb) / max(a.ram_gb, b.ram_gb)
            score += 0.05 if ratio >= 0.75 else -0.04
        if a.storage_gb and b.storage_gb:
            ratio = min(a.storage_gb, b.storage_gb) / max(a.storage_gb, b.storage_gb)
            score += 0.05 if ratio >= 0.5 else -0.03

        return max(0.0, min(1.0, score))

    def _model_text(self, features: DeviceFeatures) -> str:
        text = f"{features.model or ''} {features.canonical_name or ''}".lower()
        text = re.sub(r"[^a-zа-я0-9]+", " ", text)
        tokens = [t for t in text.split() if t not in GENERIC_TOKENS and len(t) > 1]
        return " ".join(dict.fromkeys(tokens))

    @staticmethod
    def _norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    def _estimate(self, item: ListingCard, peers: list[ListingCard]) -> MarketStats:
        prices = np.array([x.price for x in peers if x.price and x.price > 0], dtype=float)
        if prices.size < self.minimum_comparables:
            return MarketStats(comparable_count=int(prices.size), confidence=0.0)

        median = float(np.median(prices))
        mad = float(np.median(np.abs(prices - median)))
        if prices.size >= 5 and mad > 0:
            robust_z = 0.6745 * np.abs(prices - median) / mad
            trimmed = prices[robust_z <= 3.5]
            if trimmed.size >= self.minimum_comparables:
                prices = trimmed
                median = float(np.median(prices))
                mad = float(np.median(np.abs(prices - median)))

        p25 = float(np.percentile(prices, 25))
        p75 = float(np.percentile(prices, 75))
        expected_resale = median * 0.95
        discount = None
        expected_profit = None
        if item.price and median > 0:
            discount = (median - item.price) / median
            expected_profit = expected_resale - item.price

        count_conf = min(1.0, math.log2(prices.size + 1) / 4.0)
        spread_ratio = (p75 - p25) / median if median else 1.0
        spread_conf = max(0.1, min(1.0, 1.0 - spread_ratio))
        confidence = round(0.7 * count_conf + 0.3 * spread_conf, 3)
        closest = sorted(peers, key=lambda x: abs((x.price or median) - median))[:12]

        return MarketStats(
            comparable_count=int(prices.size),
            median_price=round(median, 2),
            p25_price=round(p25, 2),
            p75_price=round(p75, 2),
            robust_spread=round(mad, 2),
            discount_pct=round(discount, 4) if discount is not None else None,
            expected_resale_price=round(expected_resale, 2),
            expected_profit=round(expected_profit, 2) if expected_profit is not None else None,
            confidence=confidence,
            comparable_ids=[x.external_id for x in closest],
        )
