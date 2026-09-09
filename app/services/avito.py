from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.config import Settings
from app.schemas import ListingCard, ListingDetails, NormalizedSearchSpec


CATEGORY_PATHS = {
    "laptops": "noutbuki",
    "phones": "telefony",
    "tablets": "planshety_i_elektronnye_knigi",
    "desktops": "nastolnye_kompyutery",
    "cameras": "fototehnika",
    "electronics": "bytovaya_elektronika",
    "other": "",
}


class AvitoError(RuntimeError):
    pass


class AvitoBlocked(AvitoError):
    pass


@dataclass(slots=True)
class ProxyConfig:
    server: str
    username: str | None = None
    password: str | None = None

    def playwright(self) -> dict[str, str]:
        value = {"server": self.server}
        if self.username:
            value["username"] = self.username
        if self.password:
            value["password"] = self.password
        return value


def parse_proxy(raw: str) -> ProxyConfig:
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
        raise ValueError("Proxy must look like http://user:pass@host:port or socks5://host:port")
    server = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    return ProxyConfig(server=server, username=parsed.username, password=parsed.password)


def _money(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _external_id(url: str, fallback: str = "") -> str:
    match = re.search(r"_(\d{6,})(?:\?|$)", url)
    if match:
        return match.group(1)
    return fallback or str(abs(hash(url)))


class AvitoCrawler:
    """Conservative Playwright crawler with pacing, proxy support and stable data-marker selectors."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._proxy_cursor = 0
        self._seen_hosts: set[str] = set()
        self._request_lock = asyncio.Lock()
        self._last_navigation_at = 0.0

    def _choose_proxy(self) -> ProxyConfig | None:
        proxy_urls = self.settings.avito_proxy_list
        if not proxy_urls:
            return None
        raw = proxy_urls[self._proxy_cursor % len(proxy_urls)]
        self._proxy_cursor += 1
        return parse_proxy(raw)

    async def _wait_for_request_slot(self) -> None:
        elapsed = time.monotonic() - self._last_navigation_at
        wait_for = self.settings.avito_min_request_interval_s - elapsed
        if wait_for > 0:
            await asyncio.sleep(wait_for)

    async def _navigate(self, page: Page, url: str) -> None:
        async with self._request_lock:
            await self._wait_for_request_slot()
            await page.goto(url, wait_until="domcontentloaded")
            self._last_navigation_at = time.monotonic()
        if self.settings.avito_post_load_delay_s > 0:
            await asyncio.sleep(self.settings.avito_post_load_delay_s)

    def _reset_saved_session(self) -> None:
        if not self.settings.avito_reset_session_each_request:
            return
        try:
            self.settings.avito_storage_state_path.unlink(missing_ok=True)
        except Exception:
            pass

    def build_search_url(
        self,
        spec: NormalizedSearchSpec,
        page: int = 1,
        custom_url: str | None = None,
    ) -> str:
        if custom_url:
            sep = "&" if "?" in custom_url else "?"
            return f"{custom_url}{sep}p={page}" if page > 1 else custom_url
        path = CATEGORY_PATHS.get(spec.category, "")
        base = f"https://www.avito.ru/{spec.city}"
        if path:
            base += f"/{path}"
        params: list[str] = []
        if spec.query:
            params.append(f"q={quote_plus(spec.query)}")
        if spec.min_price is not None:
            params.append(f"pmin={spec.min_price}")
        if spec.max_price is not None:
            params.append(f"pmax={spec.max_price}")
        params.append("s=104")
        if page > 1:
            params.append(f"p={page}")
        return base + ("?" + "&".join(params) if params else "")

    async def _new_context(self, browser: Browser) -> BrowserContext:
        kwargs = {
            "locale": self.settings.avito_locale,
            "timezone_id": self.settings.avito_timezone,
            "viewport": {"width": 1440, "height": 1000},
            "extra_http_headers": {
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6",
                "DNT": "1",
            },
        }
        if self.settings.avito_user_agent:
            kwargs["user_agent"] = self.settings.avito_user_agent
        if (
            self.settings.avito_persist_session
            and not self.settings.avito_reset_session_each_request
            and self.settings.avito_storage_state_path.exists()
        ):
            kwargs["storage_state"] = str(self.settings.avito_storage_state_path)
        context = await browser.new_context(**kwargs)
        if self.settings.avito_block_heavy_resources:
            async def block_heavy(route):
                if route.request.resource_type in {"image", "media", "font"}:
                    await route.abort()
                else:
                    await route.continue_()
            await context.route("**/*", block_heavy)
        return context

    def _track_page_hosts(self, page: Page) -> None:
        def on_request(request) -> None:
            try:
                host = urlparse(request.url).hostname
                if host:
                    self._seen_hosts.add(host)
            except Exception:
                pass

        page.on("request", on_request)

    def _save_seen_hosts(self) -> None:
        try:
            self.settings.avito_hosts_log_path.write_text(
                "\n".join(sorted(self._seen_hosts)) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass

    async def _page_state(self, page: Page) -> str:
        try:
            text = (await page.locator("body").inner_text()).lower()
        except Exception:
            return "navigating"

        blocked_signals = (
            "доступ ограничен",
            "подозрительная активность",
            "пройдите проверку",
            "captcha",
            "security check",
            "temporarily blocked",
            "необычная активность",
        )
        redirect_signals = (
            "перенаправляем",
            "перенаправление",
            "redirecting",
        )
        if any(signal in text for signal in blocked_signals):
            return "blocked"
        if any(signal in text for signal in redirect_signals):
            return "redirecting"

        host = urlparse(page.url).hostname or ""
        if host.endswith("avito.ru") and len(text.strip()) > 300:
            return "ready"
        return "navigating"

    async def _save_session(self, context: BrowserContext) -> None:
        if not self.settings.avito_persist_session:
            return
        try:
            await context.storage_state(path=str(self.settings.avito_storage_state_path))
        except Exception:
            pass

    async def _guard_block(self, page: Page, context: BrowserContext) -> None:
        state = await self._page_state(page)
        if state == "ready":
            await self._save_session(context)
            return

        if state == "blocked":
            path = self.settings.blocked_screenshot_dir / f"avito-blocked-{int(time.time())}.png"
            try:
                await page.screenshot(path=str(path), full_page=True)
            except Exception:
                pass

        if self.settings.avito_headless:
            raise AvitoBlocked(
                "Avito показал CAPTCHA/block page. Установите AVITO_HEADLESS=false и "
                "при необходимости решите CAPTCHA вручную в открывшемся Chromium. "
                "В low-request режиме сессия после запроса не сохраняется."
            )

        deadline = time.monotonic() + self.settings.avito_manual_captcha_timeout_s
        consecutive_ready = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(2)
            state = await self._page_state(page)
            if state == "ready":
                consecutive_ready += 1
                if consecutive_ready >= 3:
                    await self._save_session(context)
                    return
            else:
                consecutive_ready = 0

        self._save_seen_hosts()
        raise AvitoBlocked(
            "Avito не завершил проверку: CAPTCHA/redirect продолжаются или снова появился "
            "'Доступ ограничен'. Хосты текущей сессии сохранены в data/avito_hosts.txt."
        )

    async def search(
        self,
        spec: NormalizedSearchSpec,
        pages: int,
        custom_url: str | None = None,
        progress_callback=None,
    ) -> list[ListingCard]:
        pages = min(max(1, pages), self.settings.avito_max_pages)
        result: dict[str, ListingCard] = {}
        proxy = self._choose_proxy()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.settings.avito_headless,
                proxy=proxy.playwright() if proxy else None,
                args=["--disable-dev-shm-usage"],
            )
            try:
                for page_no in range(1, pages + 1):
                    self._reset_saved_session()
                    context = await self._new_context(browser)
                    page = await context.new_page()
                    self._track_page_hosts(page)
                    page.set_default_timeout(self.settings.avito_timeout_ms)
                    try:
                        await self._navigate(
                            page,
                            self.build_search_url(spec, page_no, custom_url),
                        )
                        await self._guard_block(page, context)
                        page_cards = await self._extract_search_page(page, page_no)
                        for card in page_cards:
                            result.setdefault(card.external_id, card)
                            if len(result) >= self.settings.avito_max_items:
                                if progress_callback:
                                    await progress_callback(page_no, pages, len(result))
                                return list(result.values())
                        if progress_callback:
                            await progress_callback(page_no, pages, len(result))
                    finally:
                        self._save_seen_hosts()
                        await context.close()
            finally:
                await browser.close()

        return list(result.values())

    async def _extract_search_page(self, page: Page, page_no: int) -> list[ListingCard]:
        soup = BeautifulSoup(await page.content(), "lxml")
        cards: list[ListingCard] = []
        for idx, node in enumerate(soup.select('[data-marker="item"]')):
            link = node.select_one('[data-marker="item-title"]') or node.select_one("a[href]")
            if not link or not link.get("href"):
                continue
            url = urljoin("https://www.avito.ru", link.get("href"))
            title = link.get_text(" ", strip=True)
            if not title:
                continue
            price_node = (
                node.select_one('[data-marker="item-price"]')
                or node.select_one('[itemprop="price"]')
            )
            price_text = ""
            if price_node:
                price_text = price_node.get("content", "") or price_node.get_text(" ", strip=True)
            location_node = (
                node.select_one('[data-marker="item-address"]')
                or node.select_one('[data-marker="item-location"]')
            )
            desc_node = node.select_one('[data-marker="item-description"]')
            image_urls = []
            for image in node.select("img")[:4]:
                src = image.get("src") or image.get("data-src")
                if src and src.startswith("http"):
                    image_urls.append(src)
            ext = node.get("data-item-id") or _external_id(url, f"{page_no}-{idx}")
            cards.append(
                ListingCard(
                    external_id=str(ext),
                    title=title,
                    price=_money(price_text),
                    url=url,
                    image_urls=list(dict.fromkeys(image_urls)),
                    location=location_node.get_text(" ", strip=True) if location_node else "",
                    snippet=desc_node.get_text(" ", strip=True) if desc_node else "",
                    source_page=page_no,
                )
            )
        return cards

    async def enrich(self, cards: list[ListingCard], limit: int | None = None) -> list[ListingDetails]:
        if not cards:
            return []
        limit = min(
            limit or self.settings.avito_max_details,
            self.settings.avito_max_details,
            len(cards),
        )
        proxy = self._choose_proxy()
        details: list[ListingDetails] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.settings.avito_headless,
                proxy=proxy.playwright() if proxy else None,
                args=["--disable-dev-shm-usage"],
            )
            context = await self._new_context(browser)
            page = await context.new_page()
            page.set_default_timeout(self.settings.avito_timeout_ms)
            try:
                for card in cards[:limit]:
                    await self._navigate(page, card.url)
                    await self._guard_block(page, context)
                    details.append(await self._extract_details(page, card))
            finally:
                self._save_seen_hosts()
                await context.close()
                await browser.close()
        return details

    async def _extract_details(self, page: Page, card: ListingCard) -> ListingDetails:
        soup = BeautifulSoup(await page.content(), "lxml")

        def text(selector: str) -> str:
            node = soup.select_one(selector)
            return node.get_text(" ", strip=True) if node else ""

        title = text('[data-marker="item-view/title-info"]') or text("h1") or card.title
        price_text = text('[data-marker="item-view/item-price"]')
        description = text('[data-marker="item-view/item-description"]')
        params_text = text('[data-marker="item-view/item-params"]')
        if not params_text:
            params_text = " | ".join(
                node.get_text(" ", strip=True)
                for node in soup.select('[data-marker^="item-view/item-params"] li')
            )
        seller_name = (
            text('[data-marker="seller-info/name"]')
            or text('[data-marker="seller-info/value"]')
            or card.seller_name
        )
        seller_blob = text('[data-marker="seller-info"]')
        rating_match = re.search(r"([1-5](?:[\.,]\d)?)", seller_blob)
        reviews_match = re.search(r"(\d[\d\s]*)\s+(?:отзыв|оцен)", seller_blob.lower())
        image_urls = list(card.image_urls)
        for image in soup.select("img"):
            src = image.get("src") or image.get("data-src")
            if src and src.startswith("http"):
                image_urls.append(src)
        body_text = soup.get_text(" ", strip=True).lower()
        seller_type = (
            "business"
            if any(x in seller_blob.lower() for x in ("компания", "магазин", "профессионал"))
            else "private"
        )
        return ListingDetails(
            **card.model_dump(exclude={"title", "price", "image_urls", "seller_name"}),
            title=title,
            price=_money(price_text) or card.price,
            image_urls=list(dict.fromkeys(image_urls))[:12],
            seller_name=seller_name,
            description=description,
            params_text=params_text,
            seller_rating=float(rating_match.group(1).replace(",", ".")) if rating_match else None,
            seller_reviews=int(re.sub(r"\D", "", reviews_match.group(1))) if reviews_match else None,
            seller_type=seller_type,
            delivery_available="доставка" in body_text,
        )
