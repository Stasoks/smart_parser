const $ = id => document.getElementById(id);
let latest = [];

const money = v => v == null ? "—" : new Intl.NumberFormat("ru-RU").format(Math.round(v)) + " ₽";
const pct = v => v == null ? "—" : Math.round(v * 100) + "%";
const esc = s => String(s ?? "").replace(/[&<>'"]/g, c => ({
  "&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"
}[c]));

async function health() {
  try {
    const x = await fetch("/api/health").then(r => r.json());
    $("health").textContent =
      `OpenRouter: ${x.ai_configured ? "OK" : "нет ключа"} · Avito proxy: ${x.proxy_configured ? "есть" : "direct"}`;
  } catch {
    $("health").textContent = "API недоступен";
  }
}

$("search-form").addEventListener("submit", async e => {
  e.preventDefault();
  $("submit").disabled = true;
  $("results-section").classList.add("hidden");
  $("progress").classList.remove("hidden");

  const payload = {
    prompt: $("prompt").value.trim(),
    city: $("city").value,
    category: $("category").value,
    max_price: Number($("max-price").value) || null,
    min_expected_profit: Number($("min-profit").value) || 0,
    pages: Number($("pages").value) || 1,
    result_limit: Number($("result-limit").value) || 25,
    custom_avito_url: $("custom-url").value.trim() || null,
    enable_web_research: false,
    enable_vision: false
  };

  try {
    const r = await fetch("/api/search", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify(payload)
    });
    const j = await r.json();
    poll(j.id);
  } catch (err) {
    show({stage:"Ошибка", progress:1, message:String(err), error:String(err)});
    $("submit").disabled = false;
  }
});

async function poll(id) {
  const tick = async () => {
    const j = await fetch("/api/jobs/" + id).then(r => r.json());
    show(j);
    if (j.status === "done") {
      latest = j.results || [];
      render();
      $("submit").disabled = false;
      return true;
    }
    if (j.status === "error") {
      $("submit").disabled = false;
      return true;
    }
    return false;
  };

  if (await tick()) return;
  const timer = setInterval(async () => {
    if (await tick()) clearInterval(timer);
  }, 800);
}

function show(j) {
  const p = Math.round((j.progress || 0) * 100);
  $("stage").textContent = j.stage || "Ожидание";
  $("message").textContent = j.message || "";
  $("percent").textContent = p + "%";
  $("bar").style.width = p + "%";

  if (j.search_spec) {
    const s = j.search_spec;
    $("spec").textContent =
      `${s.query || "без текстового запроса"} · ${s.category} · ${s.city} · до ${money(s.max_price)} · маржа от ${money(s.min_expected_profit)}`;
  }
}

function render() {
  $("results-section").classList.remove("hidden");
  $("count").textContent = latest.length + " ссылок";

  if (!latest.length) {
    $("results").innerHTML =
      '<div class="panel muted">Надёжных предложений по заданным условиям не найдено.</div>';
    return;
  }

  $("results").innerHTML = latest.map((fm, i) => {
    const l = fm.listing || {};
    const a = fm.analysis || {};
    const m = fm.market || {};
    const d = fm.device || {};
    const profit = a.expected_profit ?? m.expected_profit;

    return `
      <article class="link-card">
        <div class="link-rank">#${i + 1}</div>
        <div class="link-main">
          <a class="deal-link" href="${esc(l.url)}" target="_blank" rel="noopener">
            ${esc(l.title)}
          </a>
          <div class="deal-summary">${esc(a.summary || l.snippet || "")}</div>
          <div class="deal-meta">
            <span>Цена <b>${money(l.price)}</b></span>
            <span>Маржа <b>${profit == null ? "—" : "+" + money(profit)}</b></span>
            <span>Дисконт <b>${pct(m.discount_pct)}</b></span>
            <span>Похожих <b>${m.comparable_count || 0}</b></span>
            ${d.cpu ? `<span>CPU <b>${esc(d.cpu)}</b></span>` : ""}
          </div>
        </div>
        <div class="deal-score">${Math.round(a.deal_score || 0)}</div>
      </article>
    `;
  }).join("");
}

health();
