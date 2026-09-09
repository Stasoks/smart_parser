from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from app.schemas import DeviceFeatures, ListingCard, MarketStats
from app.services.features import identity_key


class MarketEstimator:
    def __init__(self, minimum_comparables: int = 3):
        self.minimum_comparables = minimum_comparables

    def estimate_all(
        self,
        listings: list[ListingCard],
        features: dict[str, DeviceFeatures],
    ) -> dict[str, MarketStats]:
        grouped: dict[str, list[ListingCard]] = defaultdict(list)
        for item in listings:
            key = identity_key(features[item.external_id])
            if key:
                grouped[key].append(item)

        output: dict[str, MarketStats] = {}
        for item in listings:
            key = identity_key(features[item.external_id])
            candidates = [x for x in grouped.get(key, []) if x.price and x.price > 0]
            if len(candidates) < self.minimum_comparables:
                f = features[item.external_id]
                candidates = [
                    x for x in listings
                    if x.price and x.price > 0
                    and features[x.external_id].brand == f.brand
                    and features[x.external_id].category == f.category
                ]
            output[item.external_id] = self._estimate(item, candidates)
        return output

    def _estimate(self, item: ListingCard, comparables: list[ListingCard]) -> MarketStats:
        peers = [x for x in comparables if x.external_id != item.external_id and x.price and x.price > 0]
        prices = np.array([x.price for x in peers], dtype=float)
        if prices.size == 0:
            return MarketStats()

        median = float(np.median(prices))
        mad = float(np.median(np.abs(prices - median)))
        if prices.size >= 5 and mad > 0:
            robust_z = 0.6745 * np.abs(prices - median) / mad
            trimmed = prices[robust_z <= 3.5]
            if trimmed.size >= 3:
                prices = trimmed
                median = float(np.median(prices))
                mad = float(np.median(np.abs(prices - median)))

        p25 = float(np.percentile(prices, 25))
        p75 = float(np.percentile(prices, 75))
        discount = None
        expected_profit = None
        expected_resale = median * 0.95
        if item.price and median > 0:
            discount = (median - item.price) / median
            expected_profit = expected_resale - item.price

        count_conf = min(1.0, math.log2(prices.size + 1) / 4.0)
        spread_ratio = (p75 - p25) / median if median else 1.0
        spread_conf = max(0.15, min(1.0, 1.0 - spread_ratio))
        confidence = round(0.65 * count_conf + 0.35 * spread_conf, 3)
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
