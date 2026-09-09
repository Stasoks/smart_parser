from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    prompt: str = ""
    query: str = ""
    city: str = "sankt-peterburg"
    category: str = "laptops"
    min_price: int | None = None
    max_price: int | None = 15_000
    min_expected_profit: int = 3_000
    pages: int = 2
    deep_analysis_top_n: int | None = None
    custom_avito_url: str | None = None
    enable_web_research: bool = True
    enable_vision: bool = True


class NormalizedSearchSpec(BaseModel):
    query: str
    city: str = "sankt-peterburg"
    category: str = "other"
    min_price: int | None = None
    max_price: int | None = None
    min_expected_profit: int = 0
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    notes: str = ""


class ListingCard(BaseModel):
    external_id: str
    title: str
    price: int | None = None
    url: str
    image_urls: list[str] = Field(default_factory=list)
    location: str = ""
    seller_name: str = ""
    published_text: str = ""
    snippet: str = ""
    source_page: int = 1


class ListingDetails(ListingCard):
    description: str = ""
    params_text: str = ""
    seller_rating: float | None = None
    seller_reviews: int | None = None
    seller_type: str = ""
    delivery_available: bool | None = None


class DeviceFeatures(BaseModel):
    category: str = "other"
    brand: str = ""
    model: str = ""
    canonical_name: str = ""
    cpu: str = ""
    gpu: str = ""
    ram_gb: float | None = None
    storage_gb: float | None = None
    storage_type: str = ""
    screen_inches: float | None = None
    year: int | None = None
    color: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class ConditionFeatures(BaseModel):
    state: Literal["new", "excellent", "good", "fair", "broken", "unknown"] = "unknown"
    cosmetic_score: float | None = None
    functional_score: float | None = None
    defects: list[str] = Field(default_factory=list)
    missing_parts: list[str] = Field(default_factory=list)
    repair_risk: float = 0.35
    battery_health: str = ""
    counterfeit_risk: float = 0.0


class VisionAssessment(BaseModel):
    cosmetic_score: float | None = None
    visible_defects: list[str] = Field(default_factory=list)
    missing_parts: list[str] = Field(default_factory=list)
    tampering_signs: list[str] = Field(default_factory=list)
    photo_quality: str = "unknown"
    confidence: float = 0.0


class MarketStats(BaseModel):
    comparable_count: int = 0
    median_price: float | None = None
    p25_price: float | None = None
    p75_price: float | None = None
    robust_spread: float | None = None
    discount_pct: float | None = None
    expected_resale_price: float | None = None
    expected_profit: float | None = None
    confidence: float = 0.0
    comparable_ids: list[str] = Field(default_factory=list)


class WebEvidence(BaseModel):
    query: str
    title: str
    url: str
    snippet: str = ""


class DealAnalysis(BaseModel):
    fair_price: float | None = None
    expected_sale_price: float | None = None
    expected_repairs: float = 0.0
    expected_profit: float | None = None
    deal_score: float = 0.0
    confidence: float = 0.0
    liquidity_score: float = 0.5
    risk_flags: list[str] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    summary: str = ""
    research_needed: bool = False
    research_queries: list[str] = Field(default_factory=list)


class FeatureMap(BaseModel):
    listing: ListingDetails
    device: DeviceFeatures
    condition: ConditionFeatures = Field(default_factory=ConditionFeatures)
    vision: VisionAssessment | None = None
    market: MarketStats = Field(default_factory=MarketStats)
    web_evidence: list[WebEvidence] = Field(default_factory=list)
    analysis: DealAnalysis | None = None
    raw_ai: dict[str, Any] = Field(default_factory=dict)


class JobState(BaseModel):
    id: str
    status: Literal["queued", "running", "done", "error"] = "queued"
    stage: str = "queued"
    progress: float = 0.0
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    error: str | None = None
    results: list[FeatureMap] = Field(default_factory=list)
    search_spec: NormalizedSearchSpec | None = None


class HealthResponse(BaseModel):
    ok: bool = True
    ai_configured: bool
    proxy_configured: bool
    web_search_provider: str
