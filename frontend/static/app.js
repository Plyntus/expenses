const OTHER_CATEGORY = "Другое";
const FALLBACK_CATEGORY = "Без категории";
const FALLBACK_SUBCATEGORY = "Без субкатегории";

const moneyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const categoryColors = [
  "#0072B2",
  "#E69F00",
  "#009E73",
  "#CC79A7",
  "#56B4E9",
  "#D55E00",
  "#1B9E77",
  "#7570B3",
  "#E7298A",
  "#66A61E",
  "#000000",
  "#A6761D",
  "#0F766E",
  "#7C3AED",
  "#B45309",
  "#0369A1",
];

let dashboardState = {
  lastSync: null,
  googleSheetsUrl: null,
  expenses: [],
  selectedCategory: null,
  collapsedCategories: new Set(),
};
let resizeTimer = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed: ${response.status}`);
  }
  return response.json();
}

function normalizeText(value, fallback = "") {
  const text = value == null ? "" : String(value).trim();
  return text || fallback;
}

function expenseAmount(expense) {
  return Math.abs(Number(expense.amount || 0));
}

function signedAmount(expense) {
  return Number(expense.amount || 0);
}

function currencySymbol(currency) {
  const code = normalizeText(currency).toUpperCase();
  if (code === "EUR") return "€";
  if (code === "USD") return "$";
  if (code === "GBP") return "£";
  if (code === "RUB") return "₽";
  return "";
}

function formatMoney(value, currency, { signed = false } = {}) {
  const numeric = Number(value || 0);
  const sign = signed && numeric < 0 ? "-" : "";
  const amount = moneyFormatter.format(Math.abs(numeric)).replace(/,/g, " ");
  const symbol = currencySymbol(currency);
  if (symbol) return `${sign}${symbol}${amount}`;
  return currency ? `${sign}${amount} ${currency}` : `${sign}${amount}`;
}

function formatSignedNumber(value) {
  const numeric = Number(value || 0);
  const sign = numeric < 0 ? "-" : "";
  return `${sign}${moneyFormatter.format(Math.abs(numeric)).replace(/,/g, " ")}`;
}

function formatDate(value) {
  if (!value) return "-";
  const [year, month, day] = String(value).split("-");
  return year && month && day ? `${day}.${month}.${year}` : String(value);
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("ru-RU");
}

function getMultiSelectedValues(id) {
  return [...document.querySelectorAll(`#${id} input[type="checkbox"]:checked`)].map(
    (input) => input.value,
  );
}

function closeOtherMultiSelects(activeId) {
  for (const select of document.querySelectorAll(".multi-select.is-open")) {
    if (select.id !== activeId) {
      select.classList.remove("is-open");
      select.querySelector(".multi-select-trigger")?.setAttribute("aria-expanded", "false");
    }
  }
}

function renderMultiSelectSummary(container, selectedValues) {
  const summary = container.querySelector(".multi-select-summary");
  const placeholder = container.dataset.placeholder || "Выберите";
  if (!selectedValues.length) {
    summary.textContent = placeholder;
    summary.classList.add("is-placeholder");
    return;
  }
  summary.textContent = selectedValues.length <= 2
    ? selectedValues.join(", ")
    : `${selectedValues.slice(0, 2).join(", ")} +${selectedValues.length - 2}`;
  summary.classList.remove("is-placeholder");
}

function setMultiSelectOptions(id, values, selectedValues = []) {
  const container = document.getElementById(id);
  const selected = new Set(selectedValues);
  const label = document.getElementById(`${id}Label`)?.textContent || "Фильтр";
  container.innerHTML = "";
  container.setAttribute("role", "group");
  container.setAttribute("aria-labelledby", `${id}Label`);

  const trigger = document.createElement("div");
  trigger.className = "multi-select-trigger";
  trigger.setAttribute("role", "button");
  trigger.setAttribute("tabindex", "0");
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");

  const summary = document.createElement("span");
  summary.className = "multi-select-summary";
  trigger.appendChild(summary);

  const clear = document.createElement("span");
  clear.className = "multi-select-clear";
  clear.setAttribute("role", "button");
  clear.setAttribute("tabindex", "0");
  clear.setAttribute("aria-label", `Сбросить ${label.toLowerCase()}`);
  clear.textContent = "x";
  trigger.appendChild(clear);

  const arrow = document.createElement("span");
  arrow.className = "multi-select-arrow";
  arrow.setAttribute("aria-hidden", "true");
  trigger.appendChild(arrow);

  const menu = document.createElement("div");
  menu.className = "multi-select-menu";
  menu.setAttribute("role", "listbox");
  menu.setAttribute("aria-multiselectable", "true");

  for (const value of values) {
    const option = document.createElement("label");
    option.className = "multi-select-option";
    option.setAttribute("role", "option");

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = value;
    checkbox.checked = selected.has(value);

    const text = document.createElement("span");
    text.textContent = value;

    option.appendChild(checkbox);
    option.appendChild(text);
    menu.appendChild(option);
  }

  container.appendChild(trigger);
  container.appendChild(menu);

  const sync = () => {
    const valuesNow = getMultiSelectedValues(id);
    renderMultiSelectSummary(container, valuesNow);
    clear.hidden = valuesNow.length === 0;
  };

  trigger.addEventListener("click", () => {
    const isOpen = container.classList.toggle("is-open");
    closeOtherMultiSelects(id);
    trigger.setAttribute("aria-expanded", String(isOpen));
  });
  trigger.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      trigger.click();
    }
    if (event.key === "Escape") {
      container.classList.remove("is-open");
      trigger.setAttribute("aria-expanded", "false");
    }
  });

  clear.addEventListener("click", (event) => {
    event.stopPropagation();
    for (const checkbox of container.querySelectorAll('input[type="checkbox"]')) {
      checkbox.checked = false;
    }
    sync();
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });
  clear.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      clear.click();
    }
  });

  menu.addEventListener("change", () => {
    sync();
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });

  sync();
}

function getFilters() {
  return {
    dateFrom: document.getElementById("dateFrom").value,
    dateTo: document.getElementById("dateTo").value,
    accounts: getMultiSelectedValues("accountFilter"),
    excludedCategories: getMultiSelectedValues("excludedCategoryFilter"),
    currency: document.getElementById("currencyFilter").value,
    minCategoryTotal: Math.max(0, Number(document.getElementById("minCategoryTotal").value || 0)),
  };
}

function filterExpenses(expenses) {
  const filters = getFilters();
  const accountSet = new Set(filters.accounts);
  const excludedCategorySet = new Set(filters.excludedCategories);

  return expenses.filter((expense) => {
    const category = normalizeText(expense.category, FALLBACK_CATEGORY);
    const account = normalizeText(expense.payment_method);
    const currency = normalizeText(expense.currency);

    if (filters.dateFrom && expense.date < filters.dateFrom) return false;
    if (filters.dateTo && expense.date > filters.dateTo) return false;
    if (accountSet.size && !accountSet.has(account)) return false;
    if (excludedCategorySet.has(category)) return false;
    if (filters.currency && currency !== filters.currency) return false;
    return true;
  });
}

function summarizeExpenses(expenses) {
  return {
    total: expenses.reduce((sum, expense) => sum + expenseAmount(expense), 0),
    count: expenses.length,
  };
}

function buildCategoryTotals(expenses) {
  const totals = new Map();
  for (const expense of expenses) {
    const category = normalizeText(expense.category, FALLBACK_CATEGORY);
    totals.set(category, (totals.get(category) || 0) + expenseAmount(expense));
  }
  return totals;
}

function collapseCategory(category, collapsedCategories) {
  return collapsedCategories.has(category) ? OTHER_CATEGORY : category;
}

function aggregateForChart(expenses, minCategoryTotal) {
  const categoryTotals = buildCategoryTotals(expenses);
  const collapsedCategories = new Set(
    [...categoryTotals.entries()]
      .filter(([, total]) => minCategoryTotal > 0 && total < minCategoryTotal)
      .map(([category]) => category),
  );
  const grouped = new Map();

  for (const expense of expenses) {
    const originalCategory = normalizeText(expense.category, FALLBACK_CATEGORY);
    const category = collapseCategory(originalCategory, collapsedCategories);
    const subcategory = normalizeText(expense.subcategory, FALLBACK_SUBCATEGORY);
    if (!grouped.has(category)) {
      grouped.set(category, { category, total: 0, subcategories: new Map() });
    }
    const categoryGroup = grouped.get(category);
    const amount = expenseAmount(expense);
    categoryGroup.total += amount;
    categoryGroup.subcategories.set(
      subcategory,
      (categoryGroup.subcategories.get(subcategory) || 0) + amount,
    );
  }

  const categories = [...grouped.values()].sort((left, right) => right.total - left.total);
  const subcategories = [
    ...new Set(categories.flatMap((item) => [...item.subcategories.keys()])),
  ].sort((left, right) => left.localeCompare(right, "ru"));

  return { categories, subcategories, collapsedCategories };
}

function getPrimaryCurrency(expenses) {
  return (
    document.getElementById("currencyFilter").value ||
    expenses.find((expense) => expense.currency)?.currency ||
    ""
  );
}

function renderSummary(filteredExpenses) {
  const currency = getPrimaryCurrency(dashboardState.expenses);
  const allForCurrency = dashboardState.expenses.filter(
    (expense) => !currency || normalizeText(expense.currency) === currency,
  );
  const overall = summarizeExpenses(allForCurrency);
  const filtered = summarizeExpenses(filteredExpenses);

  document.getElementById("overallSummary").textContent =
    `Всего: ${formatMoney(overall.total, currency)} · ${overall.count} транзакций`;
  document.getElementById("filteredSummary").textContent =
    `С фильтрами: ${formatMoney(filtered.total, currency)} · ${filtered.count} транзакций`;

  const lastSync = dashboardState.lastSync;
  const importedRows = lastSync?.rows_imported ?? "-";
  const status = lastSync?.status ?? "never synced";
  document.getElementById("syncMeta").textContent =
    `Загружено из Postgres · импортировано строк: ${importedRows} · статус: ${status} · ${formatDateTime(lastSync?.finished_at)}`;

  const error = document.getElementById("syncError");
  if (lastSync?.error_message) {
    error.hidden = false;
    error.textContent = lastSync.error_message;
  } else {
    error.hidden = true;
    error.textContent = "";
  }

  const sheetLink = document.getElementById("sheetLink");
  if (dashboardState.googleSheetsUrl) {
    sheetLink.href = dashboardState.googleSheetsUrl;
    sheetLink.hidden = false;
  } else {
    sheetLink.hidden = true;
  }
}

function colorForSubcategory(subcategory, index) {
  let hash = 0;
  for (let i = 0; i < subcategory.length; i += 1) {
    hash = (hash * 31 + subcategory.charCodeAt(i)) >>> 0;
  }
  return categoryColors[(hash + index) % categoryColors.length];
}

function renderChart(filteredExpenses) {
  const filters = getFilters();
  const currency = getPrimaryCurrency(filteredExpenses);
  const chartElement = document.getElementById("categoryChart");
  const chartWidth = chartElement.clientWidth || window.innerWidth;
  const compactChart = chartWidth < 1180;
  const { categories, subcategories, collapsedCategories } = aggregateForChart(
    filteredExpenses,
    filters.minCategoryTotal,
  );
  dashboardState.collapsedCategories = collapsedCategories;

  if (dashboardState.selectedCategory) {
    const selectedExists = categories.some((item) => item.category === dashboardState.selectedCategory);
    if (!selectedExists) dashboardState.selectedCategory = null;
  }

  const categoryNames = categories.map((item) => item.category);
  const totalsByCategory = new Map(categories.map((item) => [item.category, item.total]));
  const traces = subcategories.map((subcategory, index) => ({
    type: "bar",
    orientation: "h",
    name: subcategory,
    y: categoryNames,
    x: categories.map((item) => item.subcategories.get(subcategory) || 0),
    marker: { color: colorForSubcategory(subcategory, index) },
    customdata: categories.map((item) => [subcategory, totalsByCategory.get(item.category) || 0]),
    hovertemplate:
      "Категория: %{y}<br>" +
      "Субкатегория: %{customdata[0]}<br>" +
      `Сумма всего: %{customdata[1]:,.2f} ${currency}<br>` +
      `Сумма субкатегории: %{x:,.2f} ${currency}` +
      "<extra></extra>",
  }));

  const legendRows = Math.ceil(subcategories.length / Math.max(1, Math.floor(chartWidth / 160)));
  const height = Math.max(440, categoryNames.length * 48 + 110 + (compactChart ? legendRows * 28 : 0));
  const emptyAnnotations = categoryNames.length
    ? []
    : [
        {
          text: "Нет транзакций для выбранных фильтров",
          x: 0.5,
          y: 0.5,
          xref: "paper",
          yref: "paper",
          showarrow: false,
          font: { size: 16, color: "#66758a" },
        },
      ];
  Plotly.react(
    "categoryChart",
    traces,
    {
      barmode: "stack",
      height,
      margin: {
        l: compactChart ? 132 : 190,
        r: compactChart ? 12 : 24,
        t: 18,
        b: compactChart ? Math.max(86, legendRows * 28 + 54) : 42,
      },
      xaxis: {
        title: "Сумма",
        gridcolor: "#dfe8f6",
        zeroline: true,
        zerolinecolor: "#d9e2f1",
        tickprefix: currencySymbol(currency),
        separatethousands: true,
      },
      yaxis: {
        autorange: "reversed",
        categoryorder: "array",
        categoryarray: categoryNames,
        visible: Boolean(categoryNames.length),
      },
      annotations: emptyAnnotations,
      legend: {
        title: { text: "Субкатегории" },
        orientation: compactChart ? "h" : "v",
        x: compactChart ? 0 : 1,
        xanchor: compactChart ? "left" : "right",
        y: compactChart ? -0.18 : 1,
        yanchor: compactChart ? "top" : "top",
        tracegroupgap: 4,
      },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      hoverlabel: {
        bgcolor: "#ffffff",
        bordercolor: "#94a3b8",
        font: { color: "#111827" },
      },
      font: { family: "Avenir Next, Segoe UI, Arial, sans-serif", color: "#111827", size: 14 },
    },
    { displayModeBar: false, responsive: true },
  );

  if (typeof chartElement.removeAllListeners === "function") {
    chartElement.removeAllListeners("plotly_click");
  }
  chartElement.on("plotly_click", (event) => {
    const point = event?.points?.[0];
    dashboardState.selectedCategory = point?.y || null;
    renderDetails(filteredExpenses);
  });
}

function transactionBelongsToSelectedCategory(expense, selectedCategory, collapsedCategories) {
  const originalCategory = normalizeText(expense.category, FALLBACK_CATEGORY);
  if (selectedCategory === OTHER_CATEGORY) return collapsedCategories.has(originalCategory);
  return originalCategory === selectedCategory;
}

function renderDetails(filteredExpenses) {
  const section = document.getElementById("detailsSection");
  const tbody = document.getElementById("transactionRows");
  tbody.innerHTML = "";

  const selectedCategory = dashboardState.selectedCategory;
  if (!selectedCategory) {
    section.classList.add("is-hidden");
    return;
  }

  const rows = filteredExpenses
    .filter((expense) =>
      transactionBelongsToSelectedCategory(
        expense,
        selectedCategory,
        dashboardState.collapsedCategories,
      ),
    )
    .sort((left, right) => String(right.date).localeCompare(String(left.date)));

  if (!rows.length) {
    section.classList.add("is-hidden");
    return;
  }

  document.getElementById("detailsTitle").textContent = `Транзакции категории: ${selectedCategory}`;
  for (const expense of rows) {
    const tr = document.createElement("tr");
    const values = [
      formatDate(expense.date),
      normalizeText(expense.payment_method, "-"),
      formatSignedNumber(signedAmount(expense)),
      normalizeText(expense.currency, "-"),
      normalizeText(expense.comment, "-"),
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  section.classList.remove("is-hidden");
}

function renderFilterOptions() {
  const expenses = dashboardState.expenses;
  const accounts = [
    ...new Set(expenses.map((expense) => normalizeText(expense.payment_method)).filter(Boolean)),
  ].sort((left, right) => left.localeCompare(right, "ru"));
  const categories = [
    ...new Set(expenses.map((expense) => normalizeText(expense.category, FALLBACK_CATEGORY))),
  ].sort((left, right) => left.localeCompare(right, "ru"));
  const currencies = [
    ...new Set(expenses.map((expense) => normalizeText(expense.currency)).filter(Boolean)),
  ].sort();

  setMultiSelectOptions("accountFilter", accounts, getMultiSelectedValues("accountFilter"));
  setMultiSelectOptions(
    "excludedCategoryFilter",
    categories,
    getMultiSelectedValues("excludedCategoryFilter"),
  );

  const currencySelect = document.getElementById("currencyFilter");
  const previousCurrency = currencySelect.value;
  currencySelect.innerHTML = "";
  for (const currency of currencies) {
    const option = document.createElement("option");
    option.value = currency;
    option.textContent = currency;
    currencySelect.appendChild(option);
  }
  currencySelect.value = currencies.includes(previousCurrency)
    ? previousCurrency
    : currencies.includes("EUR")
      ? "EUR"
      : currencies[0] || "";

  const dates = expenses.map((expense) => expense.date).filter(Boolean).sort();
  if (dates.length) {
    const dateFrom = document.getElementById("dateFrom");
    const dateTo = document.getElementById("dateTo");
    dateFrom.min = dates[0];
    dateFrom.max = dates[dates.length - 1];
    dateTo.min = dates[0];
    dateTo.max = dates[dates.length - 1];
    if (!dateFrom.value) dateFrom.value = dates[0];
    if (!dateTo.value) dateTo.value = dates[dates.length - 1];
  }
}

function renderDashboard() {
  const filteredExpenses = filterExpenses(dashboardState.expenses);
  renderSummary(filteredExpenses);
  renderChart(filteredExpenses);
  renderDetails(filteredExpenses);
}

async function loadDashboard() {
  const summary = await api("/api/dashboard/summary");
  const expenses = await api("/api/expenses?limit=50000");
  dashboardState = {
    ...dashboardState,
    lastSync: summary.last_sync,
    googleSheetsUrl: summary.google_sheets_url,
    expenses,
  };
  renderFilterOptions();
  renderDashboard();
}

async function syncFromSheets() {
  const button = document.getElementById("syncButton");
  button.disabled = true;
  button.textContent = "Синхронизация...";
  try {
    await api("/api/sync/google-sheets", { method: "POST" });
    dashboardState.selectedCategory = null;
    await loadDashboard();
  } catch (error) {
    const box = document.getElementById("syncError");
    box.hidden = false;
    box.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Синхронизировать";
  }
}

function resetFilters() {
  document.getElementById("dateFrom").value = "";
  document.getElementById("dateTo").value = "";
  for (const checkbox of document.querySelectorAll(".multi-select input[type='checkbox']")) {
    checkbox.checked = false;
  }
  document.getElementById("minCategoryTotal").value = "";
  dashboardState.selectedCategory = null;
  renderFilterOptions();
  renderDashboard();
}

for (const id of [
  "dateFrom",
  "dateTo",
  "accountFilter",
  "excludedCategoryFilter",
  "currencyFilter",
  "minCategoryTotal",
]) {
  document.getElementById(id).addEventListener("change", () => {
    dashboardState.selectedCategory = null;
    renderDashboard();
  });
}

document.getElementById("syncButton").addEventListener("click", syncFromSheets);
document.getElementById("resetFilters").addEventListener("click", resetFilters);
document.addEventListener("click", (event) => {
  if (!event.target.closest(".multi-select")) closeOtherMultiSelects(null);
});
window.addEventListener("resize", () => {
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(renderDashboard, 120);
});

loadDashboard().catch((error) => {
  const box = document.getElementById("syncError");
  box.hidden = false;
  box.textContent = error.message;
});
