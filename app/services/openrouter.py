from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas import (
    ConditionFeatures,
    DealAnalysis,
    DeviceFeatures,
    FeatureMap,
    ListingCard,
    ListingDetails,
    NormalizedSearchSpec,
    SearchRequest,
    VisionAssessment,
)
from app.services.features import local_extract_condition, local_extract_device


class OpenRouterService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.ai_enabled and self.settings.openrouter_api_key)

    async def _chat(self, messages: list[dict[str, Any]], *, vision: bool = False) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        model = self.settings.openrouter_vision_model if vision else self.settings.openrouter_model
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.openrouter_site_url,
            "X-Title": self.settings.openrouter_app_name,
        }
        async with httpx.AsyncClient(timeout=self.settings.openrouter_timeout_s) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return _json_from_text(str(content))

    async def interpret_search(self, request: SearchRequest) -> NormalizedSearchSpec:
        fallback = NormalizedSearchSpec(
            query=request.query or request.prompt,
            city=request.city,
            category=request.category,
            min_price=request.min_price,
            max_price=request.max_price,
            min_expected_profit=request.min_expected_profit,
        )
        if not self.enabled or not request.prompt.strip():
            return fallback
        try:
            raw = await self._chat([
                {
                    "role": "system",
                    "content": (
                        "Convert a Russian marketplace request into strict JSON. Return query, city, category, "
                        "min_price, max_price, min_expected_profit, include_keywords, exclude_keywords, "
                        "preferred_brands, notes. Preserve explicit UI values unless the prompt overrides them. "
                        "Allowed categories: laptops, phones, tablets, desktops, cameras, electronics, other."
                    ),
                },
                {"role": "user", "content": json.dumps(
                    {"prompt": request.prompt, "explicit": fallback.model_dump()},
                    ensure_ascii=False,
                )},
            ])
            merged = fallback.model_dump()
            merged.update({k: v for k, v in raw.items() if v is not None})
            return NormalizedSearchSpec.model_validate(merged)
        except Exception:
            return fallback

    async def normalize_batch(
        self,
        listings: list[ListingCard],
        category: str,
    ) -> dict[str, tuple[DeviceFeatures, ConditionFeatures]]:
        fallback = {
            item.external_id: (local_extract_device(item, category), local_extract_condition(item))
            for item in listings
        }
        if not listings or not self.enabled:
            return fallback
        items = [
            {"id": x.external_id, "title": x.title, "snippet": x.snippet, "price": x.price}
            for x in listings
        ]
        try:
            raw = await self._chat([
                {
                    "role": "system",
                    "content": (
                        'Normalize used electronics. Return JSON {"items":[...]}. Each item has id, device and condition. '
                        "device: category, brand, model, canonical_name, cpu, gpu, ram_gb, storage_gb, storage_type, "
                        "screen_inches, year, color, extra. condition: state, cosmetic_score, functional_score, defects, "
                        "missing_parts, repair_risk, battery_health, counterfeit_risk. Never invent missing specs."
                    ),
                },
                {"role": "user", "content": json.dumps({"category": category, "items": items}, ensure_ascii=False)},
            ])
            for item in raw.get("items", []):
                item_id = str(item.get("id", ""))
                if item_id not in fallback:
                    continue
                try:
                    fallback[item_id] = (
                        DeviceFeatures.model_validate(item.get("device", {})),
                        ConditionFeatures.model_validate(item.get("condition", {})),
                    )
                except ValidationError:
                    continue
        except Exception:
            pass
        return fallback

    async def vision_assess(self, listing: ListingDetails) -> VisionAssessment | None:
        if not self.enabled or not listing.image_urls:
            return None
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                "Inspect these marketplace photos. Return JSON with cosmetic_score 0..1, visible_defects, "
                "missing_parts, tampering_signs, photo_quality, confidence 0..1. Only report visually supported facts."
            ),
        }]
        for url in listing.image_urls[: self.settings.vision_max_images]:
            content.append({"type": "image_url", "image_url": {"url": url}})
        try:
            raw = await self._chat([
                {"role": "system", "content": "You are a careful visual inspector of second-hand electronics."},
                {"role": "user", "content": content},
            ], vision=True)
            return VisionAssessment.model_validate(raw)
        except Exception:
            return None

    async def analyze_deal(self, feature_map: FeatureMap) -> DealAnalysis:
        market = feature_map.market
        fallback_discount = max(0.0, market.discount_pct or 0.0)
        fallback = DealAnalysis(
            fair_price=market.median_price,
            expected_sale_price=market.expected_resale_price,
            expected_profit=market.expected_profit,
            deal_score=min(100.0, fallback_discount * 160 * market.confidence),
            confidence=market.confidence,
            risk_flags=list(feature_map.condition.defects),
            summary="Статистическая оценка без LLM." if not self.enabled else "",
        )
        if not self.enabled:
            return fallback
        compact = feature_map.model_dump(mode="json", exclude={"analysis", "raw_ai"})
        try:
            raw = await self._chat([
                {
                    "role": "system",
                    "content": (
                        "Evaluate this used-electronics deal for resale. Return strict JSON with fair_price, "
                        "expected_sale_price, expected_repairs, expected_profit, deal_score 0..100, confidence 0..1, "
                        "liquidity_score 0..1, risk_flags, positives, summary, research_needed, research_queries. "
                        "Be conservative and use the robust market statistics as the anchor."
                    ),
                },
                {"role": "user", "content": json.dumps(compact, ensure_ascii=False)},
            ])
            analysis = DealAnalysis.model_validate(raw)
            if analysis.expected_profit is None and feature_map.listing.price and analysis.expected_sale_price:
                analysis.expected_profit = (
                    analysis.expected_sale_price
                    - feature_map.listing.price
                    - analysis.expected_repairs
                )
            return analysis
        except Exception:
            return fallback


def _json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("~~~"):
        text = re.sub(r"^~~~(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*~~~$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
