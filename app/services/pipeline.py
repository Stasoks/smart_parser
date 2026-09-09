from __future__ import annotations

from sqlmodel import Session

from app.config import Settings
from app.db import SavedListing, SearchRun, engine
from app.schemas import DealAnalysis, FeatureMap, ListingDetails, SearchRequest
from app.services.avito import AvitoCrawler
from app.services.features import (
    local_extract_condition,
    local_extract_device,
    screen_listing,
)
from app.services.jobs import JobManager
from app.services.market import MarketEstimator
from app.services.openrouter import OpenRouterService


class DealPipeline:
    """Fast shortlist pipeline: search cards -> local filtering -> market ranking -> links."""

    def __init__(self, settings: Settings, jobs: JobManager):
        self.settings = settings
        self.jobs = jobs
        self.avito = AvitoCrawler(settings)
        self.ai = OpenRouterService(settings)
        self.market = MarketEstimator(settings.minimum_comparables)

    async def run(self, job_id: str, request: SearchRequest) -> None:
        try:
            await self.jobs.update(
                job_id,
                status="running",
                stage="1/4 · Запрос",
                progress=0.03,
                message="Разбираю условия поиска. Это один запрос к OpenRouter.",
            )
            spec = await self.ai.interpret_search(request)
            await self.jobs.update(job_id, search_spec=spec, progress=0.08)
            self._save_run(job_id, request.prompt, spec.model_dump_json(), "running")

            async def scrape_progress(page_no: int, pages: int, found: int) -> None:
                fraction = page_no / max(1, pages)
                await self.jobs.update(
                    job_id,
                    stage="2/4 · Avito",
                    progress=0.10 + 0.42 * fraction,
                    message=f"Страница {page_no}/{pages} · найдено {found} объявлений",
                )

            await self.jobs.update(
                job_id,
                stage="2/4 · Avito",
                progress=0.10,
                message=f"Загружаю страницы Avito: 0/{request.pages}",
            )
            cards = await self.avito.search(
                spec,
                pages=request.pages,
                custom_url=request.custom_avito_url,
                progress_callback=scrape_progress,
            )
            if not cards:
                raise RuntimeError(
                    "Avito не вернул объявления. Проверьте фильтры, CAPTCHA и маршрут до Avito."
                )

            total = len(cards)
            await self.jobs.update(
                job_id,
                stage="3/4 · Фильтрация",
                progress=0.55,
                message=f"Проверяю мусорные объявления: 0/{total}",
            )

            clean_cards = []
            device_map = {}
            condition_map = {}
            rejected_reasons: dict[str, int] = {}

            for index, card in enumerate(cards, start=1):
                decision = screen_listing(card)
                if decision.rejected:
                    rejected_reasons[decision.reason] = rejected_reasons.get(decision.reason, 0) + 1
                else:
                    clean_cards.append(card)
                    device_map[card.external_id] = local_extract_device(card, spec.category)
                    condition_map[card.external_id] = local_extract_condition(card)

                if index == total or index % 10 == 0:
                    await self.jobs.update(
                        job_id,
                        progress=0.55 + 0.13 * (index / total),
                        message=(
                            f"Проверено {index}/{total} · "
                            f"оставлено {len(clean_cards)} · отсеяно {index - len(clean_cards)}"
                        ),
                    )

            if not clean_cards:
                rejected = ", ".join(f"{k}: {v}" for k, v in rejected_reasons.items())
                raise RuntimeError(f"После фильтрации не осталось объявлений. {rejected}")

            await self.jobs.update(
                job_id,
                stage="4/4 · Сравнение цен",
                progress=0.72,
                message=f"Сравниваю цены {len(clean_cards)} нормальных объявлений",
            )
            market_map = self.market.estimate_all(clean_cards, device_map)

            candidates = []
            for card in clean_cards:
                market = market_map[card.external_id]
                condition = condition_map[card.external_id]
                if market.comparable_count < self.settings.minimum_comparables:
                    continue
                if market.expected_profit is None or market.expected_profit < spec.min_expected_profit:
                    continue
                if market.discount_pct is None or market.discount_pct < 0.10:
                    continue
                if market.confidence < 0.35:
                    continue

                score = self._pre_score(market, condition)
                analysis = DealAnalysis(
                    fair_price=market.median_price,
                    expected_sale_price=market.expected_resale_price,
                    expected_profit=market.expected_profit,
                    deal_score=round(max(0.0, min(100.0, score)), 1),
                    confidence=market.confidence,
                    liquidity_score=0.5,
                    risk_flags=list(condition.defects),
                    positives=[
                        f"дисконт {round((market.discount_pct or 0) * 100)}%",
                        f"{market.comparable_count} похожих объявлений",
                    ],
                    summary=self._short_summary(card, device_map[card.external_id], market),
                )
                candidates.append(
                    FeatureMap(
                        listing=ListingDetails(**card.model_dump()),
                        device=device_map[card.external_id],
                        condition=condition,
                        market=market,
                        analysis=analysis,
                    )
                )

            candidates.sort(
                key=lambda fm: (
                    fm.analysis.deal_score if fm.analysis else 0,
                    fm.analysis.expected_profit
                    if fm.analysis and fm.analysis.expected_profit is not None
                    else -10**9,
                ),
                reverse=True,
            )
            result_limit = max(1, min(request.result_limit, 100))
            results = candidates[:result_limit]

            rejected_total = total - len(clean_cards)
            await self.jobs.update(
                job_id,
                stage="4/4 · Готово",
                progress=0.98,
                message=(
                    f"Найдено {len(results)} подходящих · "
                    f"мусорных объявлений отсеяно {rejected_total} · "
                    f"полные карточки не открывались"
                ),
            )

            self._save_results(job_id, results)
            self._save_run(
                job_id,
                request.prompt,
                spec.model_dump_json(),
                "done",
                len(results),
            )
            await self.jobs.complete(
                job_id,
                results,
                message=(
                    f"Готово: {len(results)} ссылок. "
                    f"Проверено {total}, отсеяно {rejected_total}."
                ),
            )
        except Exception as exc:
            self._save_run(job_id, request.prompt, "", "error", error=str(exc))
            await self.jobs.fail(job_id, exc)

    @staticmethod
    def _short_summary(card, device, market) -> str:
        parts = []
        if device.canonical_name:
            parts.append(device.canonical_name)
        if device.cpu:
            parts.append(device.cpu)
        specs = []
        if device.ram_gb:
            specs.append(f"{device.ram_gb:g} ГБ RAM")
        if device.storage_gb:
            specs.append(f"{device.storage_gb:g} ГБ {device.storage_type}".strip())
        if specs:
            parts.append(", ".join(specs))
        if market.median_price:
            parts.append(
                f"медиана похожих ~{round(market.median_price):,} ₽".replace(",", " ")
            )
        if market.discount_pct is not None:
            parts.append(f"ниже рынка примерно на {round(market.discount_pct * 100)}%")
        if market.expected_profit is not None:
            parts.append(
                f"ожидаемая маржа ~{round(market.expected_profit):,} ₽".replace(",", " ")
            )
        if card.snippet:
            snippet = " ".join(card.snippet.split())
            if len(snippet) > 150:
                snippet = snippet[:147] + "..."
            parts.append(snippet)
        return " · ".join(parts)

    @staticmethod
    def _pre_score(market, condition) -> float:
        discount = max(0.0, market.discount_pct or 0.0)
        profit = max(0.0, market.expected_profit or 0.0)
        confidence = max(0.0, market.confidence)
        risk_factor = max(0.15, 1 - 0.65 * condition.repair_risk)
        return (
            discount * 115
            + min(35.0, profit / 350)
            + min(15.0, market.comparable_count * 1.5)
        ) * confidence * risk_factor

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
