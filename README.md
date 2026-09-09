# Smart Parser

AI-assisted поиск недооценённых объявлений Avito для перепродажи техники.

## Текущий режим: fast shortlist

### Low-request режим Avito

По умолчанию Smart Parser делает минимум обращений к площадке:

- одна страница выдачи на поиск;
- отдельные карточки не открываются;
- перед каждой страницей создаётся новый чистый browser context;
- старый `data/avito_storage_state.json` удаляется;
- cookies/localStorage между запросами не переиспользуются;
- между двумя `page.goto()` выдерживается минимум 15 секунд, в том числе между разными запусками поиска;
- тяжёлые ресурсы (images/media/fonts) могут блокироваться для уменьшения количества сетевых обращений.

Ключевые параметры:

```env
AVITO_RESET_SESSION_EACH_REQUEST=true
AVITO_PERSIST_SESSION=false
AVITO_MIN_REQUEST_INTERVAL_S=15
AVITO_BLOCK_HEAVY_RESOURCES=true
AVITO_MAX_PAGES=2
```


По умолчанию приложение работает в быстром режиме: оно **не открывает отдельные карточки объявлений** и не запускает vision/web research для каждого кандидата. Поток такой:

```text
1. Один запрос в OpenRouter для разбора поискового запроса
2. Загрузка страниц выдачи Avito
3. Локальный отсев запчастей, опта и цен-приманок
4. Локальная нормализация и сравнение похожих моделей
5. Готовый список ссылок с коротким описанием, дисконтом и оценочной маржой
```

Это сделано намеренно: старый deep-analysis режим был слишком медленным и слишком охотно принимал каталоги/запчасти за выгодные предложения.

Smart Parser не просто сортирует объявления по цене. Он собирает рынок, нормализует модель и характеристики устройства, строит feature map, оценивает медианную цену и потенциальную маржу, а затем глубже проверяет лучшие кандидаты: открывает карточку, читает описание, анализирует фотографии через мультимодальную модель OpenRouter и при необходимости делает дополнительный поиск в интернете.

## Возможности

- Естественный запрос: `ноуты до 15к с маржой хотя бы 4к, ThinkPad/Dell/HP`.
- Категории: ноутбуки, телефоны, планшеты, компьютеры, фототехника, электроника.
- Возможность передать готовую Avito-ссылку с собственными фильтрами.
- Playwright crawler с `data-marker` селекторами и HTML fallback.
- Российские HTTP/HTTPS/SOCKS5 прокси через `AVITO_PROXY_URLS`.
- Явное распознавание block/CAPTCHA страниц вместо пустого результата.
- OpenRouter для интерпретации запроса, нормализации модели, анализа описания, vision и финального Deal Score.
- Web research через DDGS или Tavily.
- Робастная оценка рынка: median, P25/P75, MAD, discount, expected resale price.
- SQLite для истории запусков и полных feature map.
- Web UI с прогрессом, Deal Score, маржой и просмотром feature map.
- Docker и GitHub Actions.

## Pipeline

```text
Запрос пользователя
      |
      v
OpenRouter -> SearchSpec
      |
      v
Avito search pages
      |
      v
Feature normalization
(local + OpenRouter)
      |
      v
Market estimator
median / P25 / P75 / MAD
      |
      v
Pre-ranking
      |
      v
TOP-N candidates
      |
      +--> full listing details
      +--> description analysis
      +--> vision analysis
      +--> optional web research
      |
      v
Final Deal Score
      |
      v
Web UI
```

Дорогой AI-анализ не запускается на каждом объявлении. Сначала более дешёвая статистика и нормализация отбрасывают очевидный мусор, затем глубокий анализ идёт только по наиболее перспективным кандидатам.

## Feature map

Пример структуры:

```json
{
  "listing": {
    "title": "Lenovo ThinkPad T480",
    "price": 9000,
    "url": "https://www.avito.ru/..."
  },
  "device": {
    "category": "laptops",
    "brand": "Lenovo",
    "model": "ThinkPad T480",
    "canonical_name": "Lenovo ThinkPad T480",
    "cpu": "I5-8350U",
    "ram_gb": 16,
    "storage_gb": 256
  },
  "condition": {
    "state": "good",
    "defects": [],
    "repair_risk": 0.2
  },
  "vision": {
    "cosmetic_score": 0.84,
    "visible_defects": [],
    "confidence": 0.78
  },
  "market": {
    "comparable_count": 18,
    "median_price": 15000,
    "discount_pct": 0.4,
    "expected_resale_price": 14250,
    "expected_profit": 5250,
    "confidence": 0.82
  },
  "analysis": {
    "deal_score": 87,
    "expected_profit": 5000,
    "risk_flags": [],
    "summary": "Сильный дисконт при нормальном состоянии"
  }
}
```

## Avito и российский IP

Avito может ограничивать выдачу или показывать challenge/CAPTCHA для некоторых IP, в том числе нероссийских.

Для запуска через российский прокси:

```env
AVITO_PROXY_URLS=http://user:password@ru-proxy.example:8000
```

Несколько прокси:

```env
AVITO_PROXY_URLS=http://user:pass@proxy1:8000,socks5://user:pass@proxy2:1080
```

Парсер не автоматизирует решение CAPTCHA. При challenge он сохраняет debug screenshot в `data/debug/` и возвращает понятную ошибку.

## Быстрый запуск

Нужен Python 3.11+.

```bash
git clone https://github.com/Stasoks/smart_parser.git
cd smart_parser

python3 -m venv .venv
source .venv/bin/activate

pip install -e '.[dev]'
playwright install chromium

cp .env.example .env
```

Добавь OpenRouter API key:

```env
OPENROUTER_API_KEY=sk-or-...
```

Если текущий IP не российский:

```env
AVITO_PROXY_URLS=http://user:password@host:port
```

Запуск:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Открыть:

```text
http://localhost:8000
```

Либо:

```bash
make install
make dev
```

## Docker

```bash
cp .env.example .env
# заполнить OPENROUTER_API_KEY и при необходимости AVITO_PROXY_URLS
docker compose up --build
```

UI будет доступен на `http://localhost:8000`.

## OpenRouter

По умолчанию:

```env
OPENROUTER_MODEL=google/gemini-2.5-flash
OPENROUTER_VISION_MODEL=google/gemini-2.5-flash
```

Можно поставить любые доступные в OpenRouter модели. Для `OPENROUTER_VISION_MODEL` нужна модель с vision.

Без `OPENROUTER_API_KEY` приложение всё равно запускается и использует локальные regex/statistical fallback механизмы, но качество нормализации и оценки состояния будет ниже.

## Web research

По умолчанию:

```env
WEB_SEARCH_PROVIDER=ddgs
```

DDGS не требует отдельного API-ключа.

Для Tavily:

```env
WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=tvly-...
```

Модель сама решает, нужен ли внешний поиск: например для типичных дефектов конкретной модели, ревизий устройства или недостающих характеристик.

## Основная конфигурация

| Переменная | Назначение |
|---|---|
| `OPENROUTER_API_KEY` | ключ OpenRouter |
| `OPENROUTER_MODEL` | текстовая модель |
| `OPENROUTER_VISION_MODEL` | vision-модель |
| `AVITO_PROXY_URLS` | российские прокси |
| `AVITO_MIN_REQUEST_INTERVAL_S` | минимальный интервал между навигациями Avito, по умолчанию 15 секунд |
| `AVITO_RESET_SESSION_EACH_REQUEST` | новый чистый browser context перед каждой страницей |
| `AVITO_PERSIST_SESSION` | сохранять cookies/storage; в low-request режиме `false` |
| `AVITO_BLOCK_HEAVY_RESOURCES` | не загружать изображения/media/fonts для уменьшения числа запросов |
| `AVITO_MAX_PAGES` | лимит страниц |
| `AVITO_MAX_ITEMS` | лимит карточек |
| `AVITO_MAX_DETAILS` | сколько полных объявлений открывать |
| `DEEP_ANALYSIS_TOP_N` | сколько лучших кандидатов глубоко анализировать |
| `VISION_MAX_IMAGES` | фото на одно vision-исследование |
| `WEB_SEARCH_PROVIDER` | `ddgs`, `tavily`, `disabled` |
| `MINIMUM_COMPARABLES` | минимум аналогов для уверенной оценки |

## Рыночная оценка

Используются:

- median;
- P25/P75;
- MAD для отсечения далёких выбросов;
- количество аналогов для confidence;
- консервативная `expected_resale_price = median * 0.95` до AI-коррекции.

Сломанные устройства и слабая выборка аналогов штрафуются в Deal Score.

## API

Создать поиск:

```http
POST /api/search
Content-Type: application/json
```

Пример:

```json
{
  "prompt": "ThinkPad до 15к с маржой от 4к",
  "city": "sankt-peterburg",
  "category": "laptops",
  "max_price": 15000,
  "min_expected_profit": 4000,
  "pages": 2,
  "deep_analysis_top_n": 10,
  "enable_web_research": true,
  "enable_vision": true
}
```

Состояние:

```http
GET /api/jobs/{job_id}
```

Health:

```http
GET /api/health
```

## Тесты

```bash
pytest -q
ruff check .
```

GitHub Actions запускает проверки на push и pull request.

## Структура

```text
app/
├── main.py
├── config.py
├── db.py
├── schemas.py
├── services/
│   ├── avito.py
│   ├── features.py
│   ├── market.py
│   ├── openrouter.py
│   ├── web_search.py
│   ├── pipeline.py
│   └── jobs.py
└── static/
    ├── index.html
    ├── styles.css
    └── app.js
```

## Ограничения

1. Avito меняет HTML, поэтому адаптер периодически придётся обновлять.
2. Цена объявления не равна фактической цене продажи.
3. Фото не позволяют надёжно диагностировать внутренние дефекты.
4. Мало аналогов означает низкую уверенность.
5. Нероссийский IP может потребовать российский прокси.
6. CAPTCHA не обходится автоматически.
7. История цен уже сохраняется, но полноценная модель ликвидности по времени жизни объявления пока не реализована.

## Референсы

При проектировании crawler-а использованы общие подходы из существующих Avito-парсеров:

- Duff89/parser_avito
- sakitoru1x1/avito-hunter

Код Smart Parser написан отдельно и не вендорит их исходники.

## Responsible usage

Используй разумные лимиты и учитывай правила площадки. Значения задержек и лимитов по умолчанию специально консервативные. Проект не предназначен для массового агрессивного скрейпинга или автоматического обхода CAPTCHA.
