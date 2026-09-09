from __future__ import annotations

import asyncio

from sqlmodel import Session

from app.config import Settings
from app.db import SavedListing, SearchRun, engine
from app.schemas import FeatureMap, ListingDetails, SearchRequest
from app.services.avito import AvitoCrawler
from app.services.features import local_extract_condition, local_extract_device
from app.services.jobs import JobManager
from app.services.market import MarketEstimator
from app.services.openrouter import OpenRouterService
from app.services.web_search import WebSearchService


class DealPipeline:
    def __init__(self, settings: Settings, jobs: JobManager):
        self.settings = settings
        self.jobs = jobs
        self.avito = AvitoCrawler(settings)
        self.ai = OpenRouterService(settings)
        self.web = WebSearchService(settings)
        self.market = MarketEstimator(settings.minimum_comparables)

    async def run(self, job_id: str, request: SearchRequest) -> None:
        try:
            await self.jobs.update(
                job_id,
                status="running",
                stage="interpret",
                progress=0.03,
                message="Разбираю запрос",
            )
            spec = await self.ai.interpret_search(request)
            await self.jobs.update(job_id, search_spec=spec)
            self._save_run(job_id, request.prompt, spec.model_dump_json(), "running")

            await self.jobs.update(
                job_id,
                stage="scrape",
                progress=0.08,
                message="Собираю объявления Avito",
            )
            cards = await self.avito.search(
                spec,
                pages=request.pages,
                custom_url=request.custom_avito_url,
            )
            if not cards:
                raise RuntimeError(
                    "Avito не вернул объявления. Проверьте фильтры и доступ с текущего IP."
                )

            await self.jobs.update(
                job_id,
                stage="normalize",
                progress=0.28,
                message=f"Нормализую {len(cards)} объявлений",
            )
            normalized = await self._normalize(cards, spec.category)
            device_map = {k: v[0] for k, v in normalized.items()}
            condition_map = {k: v[1] for k, v in normalized.items()}
            market_map = self.market.estimate_all(cards, device_map)

            ranked_cards = sorted(
                cards,
                key=lambda x: self._pre_score(
                    market_map[x.external_id],
                    condition_map[x.external_id],
                ),
                reverse=True,
            )

            deep_n = min(
                request.deep_analysis_top_n or self.settings.deep_analysis_top_n,
                self.settings.avito_max_details,
                len(ranked_cards),
            )
            await self.jobs.update(
                job_id,
                stage="details",
                progress=0.42,
                message=f"Открываю {deep_n} лучших объявлений",
            )
            details_list = await self.avito.enrich(ranked_cards[:deep_n], limit=deep_n)
            details_by_id = {x.external_id: x for x in details_list}

            if details_list:
                detail_normalized = await self._normalize(details_list, spec.category)
                normalized.update(detail_normalized)
                device_map.update({k: v[0] for k, v in detail_normalized.items()})
                condition_map.update({k: v[1] for k, v in detail_normalized.items()})
                market_map = self.market.estimate_all(cards, device_map)

            feature_maps: list[FeatureMap] = []
            for card in ranked_cards[: max(deep_n, min(30, len(ranked_cards)))]:
                listing = details_by_id.get(card.external_id) or ListingDetails(
                    **card.model_dump()
                )
                feature_maps.append(
                    FeatureMap(
                        listing=listing,
                        device=device_map[card.external_id],
                        condition=condition_map[card.external_id],
                        market=market_map[card.external_id],
                    )
                )

            await self.jobs.update(
                job_id,
                stage="vision",
                progress=0.56,
                message="Проверяю фотографии лучших кандидатов",
            )
            if request.enable_vision and self.ai.enabled:
                await self._vision(feature_maps[:deep_n])

            await self.jobs.update(
                job_id,
                stage="analysis",
                progress=0.68,
                message="Оцениваю маржу, риски и ликвидность",
            )
            await self._analyze(feature_maps, request.enable_web_research)

            feature_maps.sort(
                key=lambda fm: (
                    fm.analysis.deal_score if fm.analysis else 0,
                    fm.analysis.expected_profit
                    if fm.analysis and fm.analysis.expected_profit is not None
                    else -10**9,
                ),
                reverse=True,
            )
            self._save_results(job_id, feature_maps)
            self._save_run(
                job_id,
                request.prompt,
                spec.model_dump_json(),
                "done",
                len(feature_maps),
            )
            await self.jobs.complete(job_id, feature_maps)
        except Exception as exc:
            self._save_run(job_id, request.prompt, "", "error", error=str(exc))
            await self.jobs.fail(job_id, exc)

    async def _normalize(self, listings, category: str):
        output = {}
        size = max(1, self.settings.llm_batch_size)
        for start in range(0, len(listings), size):
            batch = listings[start:start + size]
            output.update(await self.ai.normalize_batch(batch, category))
        for item in listings:
            output.setdefault(
                item.external_id,
                (
                    local_extract_device(item, category),
                    local_extract_condition(item),
                ),
            )
        return output

    async def _vision(self, feature_maps: list[FeatureMap]) -> None:
        semaphore = asyncio.Semaphore(3)

        async def one(fm: FeatureMap):
            async with semaphore:
                fm.vision = await self.ai.vision_assess(fm.listing)
                if fm.vision and fm.vision.visible_defects:
                    fm.condition.defects = list(
                        dict.fromkeys(fm.condition.defects + fm.vision.visible_defects)
                    )
                    fm.condition.repair_risk = max(
                        fm.condition.repair_risk,
                        min(0.95, 0.35 + 0.08 * len(fm.vision.visible_defects)),
                    )

        await asyncio.gather(*(one(fm) for fm in feature_maps))

    async def _analyze(
        self,
        feature_maps: list[FeatureMap],
        enable_web_research: bool,
    ) -> None:
        semaphore = asyncio.Semaphore(4)

        async def one(fm: FeatureMap):
            async with semaphore:
                fm.analysis = await self.ai.analyze_deal(fm)
                if (
                    enable_web_research
                    and fm.analysis.research_needed
                    and fm.analysis.research_queries
                    and self.settings.web_search_provider != "disabled"
                ):
                    fm.web_evidence = await self.web.multi_search(
                        fm.analysis.research_queries,
                        max_results_each=3,
                    )
                    if fm.web_evidence:
                        fm.analysis = await self.ai.analyze_deal(fm)
                self._apply_guardrails(fm)

        await asyncio.gather(*(one(fm) for fm in feature_maps))

    def _apply_guardrails(self, fm: FeatureMap) -> None:
        if fm.analysis is None:
            return
        price = fm.listing.price or 0
        if fm.condition.state == "broken" or fm.condition.repair_risk >= 0.85:
            fm.analysis.deal_score = min(fm.analysis.deal_score, 38.0)
            fm.analysis.risk_flags = list(
                dict.fromkeys(
                    fm.analysis.risk_flags
                    + ["Высокий риск ремонта/неисправности"]
                )
            )
        if fm.analysis.expected_sale_price is not None:
            fm.analysis.expected_profit = round(
                fm.analysis.expected_sale_price
                - price
                - fm.analysis.expected_repairs,
                2,
            )
        if fm.market.comparable_count < self.settings.minimum_comparables:
            fm.analysis.confidence = min(fm.analysis.confidence, 0.55)
            fm.analysis.deal_score *= 0.88
            fm.analysis.risk_flags = list(
                dict.fromkeys(
                    fm.analysis.risk_flags
                    + ["Мало сопоставимых объявлений"]
                )
            )
        fm.analysis.deal_score = round(
            max(0.0, min(100.0, fm.analysis.deal_score)),
            1,
        )

    @staticmethod
    def _pre_score(market, condition) -> float:
        discount = market.discount_pct or 0.0
        profit = max(0.0, market.expected_profit or 0.0)
        confidence = max(0.15, market.confidence)
        return (
            (discount * 100 + min(40, profit / 500))
            * confidence
            * (1 - 0.75 * condition.repair_risk)
        )

    @staticmethod
    def _save_run(
        job_id: str,
        prompt: str,
        spec_json: str,
        status: str,
        result_count: int = 0,
        error: str = "",
    ) -> None:
        with Session(engine) as session:
            run = session.get(SearchRun, job_id)
            if run is None:
                run = SearchRun(id=job_id, prompt=prompt)
            run.search_spec_json = spec_json or run.search_spec_json
            run.status = status
            run.result_count = result_count
            run.error = error
            session.add(run)
            session.commit()

    @staticmethod
    def _save_results(job_id: str, feature_maps: list[FeatureMap]) -> None:
        with Session(engine) as session:
            for fm in feature_maps:
                session.add(
                    SavedListing(
                        search_id=job_id,
                        external_id=fm.listing.external_id,
                        title=fm.listing.title,
                        price=fm.listing.price,
                        url=fm.listing.url,
                        deal_score=fm.analysis.deal_score if fm.analysis else 0.0,
                        expected_profit=(
                            fm.analysis.expected_profit
                            if fm.analysis
                            else fm.market.expected_profit
                        ),
                        feature_map_json=fm.model_dump_json(),
                    )
                )
            session.commit()
