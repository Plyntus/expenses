const OTHER_CATEGORY = "Другое";
const FALLBACK_CATEGORY = "Без категории";
const FALLBACK_SUBCATEGORY = "Без субкатегории";
const FALLBACK_ACCOUNT = "Без счета";
const FALLBACK_ACCOUNT_TYPE = "Без типа";
const FALLBACK_ACCOUNT_STATUS = "Без статуса";
const EUR_RATE_API_URL = "https://open.er-api.com/v6/latest/EUR";

const moneyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const wholeNumberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
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
  activeView: "expenses",
  lastSync: null,
  googleSheetsUrl: null,
  expenses: [],
  movements: [],
  selectedCategory: null,
  collapsedCategories: new Set(),
  convertToEur: false,
  exchangeRates: null,
  exchangeRateError: null,
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

function rawField(expense, key, fallback = "") {
  const raw = expense?.raw_values_json || {};
  return normalizeText(raw[key], fallback);
}

function accountName(expense) {
  return normalizeText(expense.payment_method, FALLBACK_ACCOUNT);
}

function accountType(expense) {
  return rawField(expense, "Account type", FALLBACK_ACCOUNT_TYPE);
}

function accountStatus(expense) {
  return rawField(expense, "Account status", FALLBACK_ACCOUNT_STATUS);
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

function formatWholeMoney(value, currency) {
  const amount = wholeNumberFormatter.format(Math.round(Math.abs(Number(value || 0))));
  const symbol = currencySymbol(currency);
  if (symbol) return `${symbol}${amount}`;
  return currency ? `${amount} ${currency}` : amount;
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
  if (!document.getElementById(id)) return [];
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

function getBalanceFilters() {
  return {
    dateFrom: document.getElementById("balanceDateFrom").value,
    dateTo: document.getElementById("balanceDateTo").value,
    currencies: getMultiSelectedValues("balanceCurrencyFilter"),
    accountTypes: getMultiSelectedValues("accountTypeFilter"),
    accountStatuses: getMultiSelectedValues("accountStatusFilter"),
  };
}

function filterMovements(movements) {
  const filters = getBalanceFilters();
  const currencySet = new Set(filters.currencies);
  const typeSet = new Set(filters.accountTypes);
  const statusSet = new Set(filters.accountStatuses);

  return movements.filter((movement) => {
    const currency = normalizeText(movement.currency);
    if (filters.dateFrom && movement.date < filters.dateFrom) return false;
    if (filters.dateTo && movement.date > filters.dateTo) return false;
    if (currencySet.size && !currencySet.has(currency)) return false;
    if (typeSet.size && !typeSet.has(accountType(movement))) return false;
    if (statusSet.size && !statusSet.has(accountStatus(movement))) return false;
    return true;
  });
}

async function ensureExchangeRates() {
  if (dashboardState.exchangeRates) return dashboardState.exchangeRates;
  const response = await fetch(EUR_RATE_API_URL);
  if (!response.ok) {
    throw new Error(`Не удалось загрузить курсы валют: ${response.status}`);
  }
  const payload = await response.json();
  if (!payload?.rates || payload.result === "error") {
    throw new Error("API курсов валют вернул неожиданный ответ");
  }
  dashboardState.exchangeRates = {
    base: "EUR",
    rates: { EUR: 1, ...payload.rates },
    updated: payload.time_last_update_utc || payload.time_last_update_unix || null,
  };
  dashboardState.exchangeRateError = null;
  return dashboardState.exchangeRates;
}

function convertAmountToEur(value, currency) {
  const code = normalizeText(currency).toUpperCase();
  if (!code || code === "EUR") return Number(value || 0);
  const rate = dashboardState.exchangeRates?.rates?.[code];
  if (!rate) return null;
  return Number(value || 0) / Number(rate);
}

function balanceDisplayAmount(value, currency) {
  if (!dashboardState.convertToEur) return { value, currency, converted: true };
  const converted = convertAmountToEur(value, currency);
  return { value: converted, currency: "EUR", converted: converted != null };
}

function accountBalanceKey(movement) {
  return [
    accountName(movement),
    normalizeText(movement.currency),
    accountType(movement),
    accountStatus(movement),
  ].join("\u0001");
}

function buildAccountBalances(movements) {
  const balances = new Map();
  for (const movement of movements) {
    const key = accountBalanceKey(movement);
    if (!balances.has(key)) {
      balances.set(key, {
        account: accountName(movement),
        currency: normalizeText(movement.currency),
        accountType: accountType(movement),
        accountStatus: accountStatus(movement),
        balance: 0,
        movementCount: 0,
      });
    }
    const item = balances.get(key);
    item.balance += signedAmount(movement);
    item.movementCount += 1;
  }
  return [...balances.values()].sort((left, right) =>
    left.account.localeCompare(right.account, "ru") ||
    left.currency.localeCompare(right.currency, "ru"),
  );
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

function renderSummary() {
  document.getElementById("overallSummary").classList.toggle(
    "is-hidden",
    dashboardState.activeView !== "expenses",
  );

  const currency = getPrimaryCurrency(dashboardState.expenses);
  const allForCurrency = dashboardState.expenses.filter(
    (expense) => !currency || normalizeText(expense.currency) === currency,
  );
  const overall = summarizeExpenses(allForCurrency);

  document.getElementById("overallSummary").textContent =
    `Всего: ${formatMoney(overall.total, currency)} · ${overall.count} транзакций`;

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
  const maxTotal = Math.max(0, ...categories.map((item) => item.total));
  const labelReserve = chartWidth < 520 ? 1.35 : chartWidth < 900 ? 1.24 : 1.16;
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

  const valueAnnotations = categories.map((item) => ({
    text: formatWholeMoney(item.total, currency),
    x: item.total,
    y: item.category,
    xref: "x",
    yref: "y",
    xanchor: "left",
    yanchor: "middle",
    xshift: chartWidth < 520 ? 4 : 8,
    showarrow: false,
    font: {
      size: chartWidth < 520 ? 11 : 13,
      color: "#334155",
      family: "Avenir Next, Segoe UI, Arial, sans-serif",
    },
  }));
  const height = Math.max(chartWidth < 520 ? 380 : 420, categoryNames.length * 40 + 96);
  const emptyAnnotations = categoryNames.length
    ? valueAnnotations
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
      showlegend: false,
      bargap: 0.34,
      margin: {
        l: compactChart ? 122 : 180,
        r: chartWidth < 520 ? 56 : 86,
        t: 18,
        b: compactChart ? 58 : 42,
      },
      xaxis: {
        title: "Сумма",
        gridcolor: "#dfe8f6",
        zeroline: true,
        zerolinecolor: "#d9e2f1",
        tickprefix: currencySymbol(currency),
        separatethousands: true,
        range: maxTotal > 0 ? [0, maxTotal * labelReserve] : undefined,
      },
      yaxis: {
        autorange: "reversed",
        categoryorder: "array",
        categoryarray: categoryNames,
        visible: Boolean(categoryNames.length),
        automargin: true,
        tickfont: { size: chartWidth < 520 ? 12 : 14 },
      },
      annotations: emptyAnnotations,
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

function formatBalanceValue(balance) {
  const display = balanceDisplayAmount(balance.balance, balance.currency);
  if (!display.converted) return "Нет курса";
  return formatMoney(display.value, display.currency, { signed: true });
}

function renderRateMeta() {
  const meta = document.getElementById("rateMeta");
  if (!dashboardState.convertToEur) {
    meta.hidden = true;
    meta.textContent = "";
    return;
  }
  meta.hidden = false;
  if (dashboardState.exchangeRateError) {
    meta.textContent = dashboardState.exchangeRateError;
    return;
  }
  if (!dashboardState.exchangeRates) {
    meta.textContent = "Загрузка курсов валют...";
    return;
  }
  const updated = dashboardState.exchangeRates.updated
    ? ` · обновлено: ${dashboardState.exchangeRates.updated}`
    : "";
  meta.textContent = `Конвертация через open.er-api.com${updated}`;
}

function balancesGroupedBy(balances, field) {
  const grouped = new Map();
  for (const balance of balances) {
    const key = normalizeText(balance[field], "-");
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(balance);
  }
  return [...grouped.entries()].sort(([left], [right]) => left.localeCompare(right, "ru"));
}

function renderBalanceRows(rows) {
  const list = document.createElement("div");
  list.className = "balance-list";
  for (const balance of rows) {
    const row = document.createElement("div");
    row.className = "balance-row";

    const account = document.createElement("div");
    account.className = "balance-account";
    account.textContent = balance.account;

    const currency = document.createElement("div");
    currency.className = "balance-currency";
    currency.textContent = normalizeText(balance.currency, "-");

    const amount = document.createElement("div");
    amount.className = `balance-amount${balance.balance < 0 ? " is-negative" : ""}`;
    amount.textContent = formatBalanceValue(balance);

    row.appendChild(account);
    row.appendChild(currency);
    row.appendChild(amount);
    list.appendChild(row);
  }
  return list;
}

function renderBalanceGroups(container, balances) {
  const section = document.createElement("section");
  section.className = "balance-section";

  for (const [status, statusRows] of balancesGroupedBy(balances, "accountStatus")) {
    const statusDetails = document.createElement("details");
    statusDetails.className = "balance-accordion balance-accordion-status";
    statusDetails.open = true;

    const statusSummary = document.createElement("summary");
    statusSummary.textContent = status;
    statusDetails.appendChild(statusSummary);

    for (const [type, typeRows] of balancesGroupedBy(statusRows, "accountType")) {
      const typeDetails = document.createElement("details");
      typeDetails.className = "balance-accordion balance-accordion-type";
      typeDetails.open = true;

      const typeSummary = document.createElement("summary");
      typeSummary.textContent = type;
      typeDetails.appendChild(typeSummary);
      typeDetails.appendChild(renderBalanceRows(typeRows));
      statusDetails.appendChild(typeDetails);
    }

    section.appendChild(statusDetails);
  }

  container.appendChild(section);
}

function renderBalance() {
  const container = document.getElementById("balanceContent");
  container.innerHTML = "";
  renderRateMeta();

  const filteredMovements = filterMovements(dashboardState.movements);
  const balances = buildAccountBalances(filteredMovements);

  if (!balances.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Нет движений по выбранным фильтрам";
    container.appendChild(empty);
    return;
  }

  renderBalanceGroups(container, balances);
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

function renderBalanceFilterOptions() {
  const movements = dashboardState.movements;
  const currencies = [
    ...new Set(movements.map((movement) => normalizeText(movement.currency)).filter(Boolean)),
  ].sort();
  const accountTypes = [
    ...new Set(movements.map((movement) => accountType(movement))),
  ].sort((left, right) => left.localeCompare(right, "ru"));
  const accountStatuses = [
    ...new Set(movements.map((movement) => accountStatus(movement))),
  ].sort((left, right) => left.localeCompare(right, "ru"));

  setMultiSelectOptions(
    "balanceCurrencyFilter",
    currencies,
    getMultiSelectedValues("balanceCurrencyFilter"),
  );
  setMultiSelectOptions(
    "accountTypeFilter",
    accountTypes,
    getMultiSelectedValues("accountTypeFilter"),
  );
  setMultiSelectOptions(
    "accountStatusFilter",
    accountStatuses,
    getMultiSelectedValues("accountStatusFilter"),
  );

  const dates = movements.map((movement) => movement.date).filter(Boolean).sort();
  if (dates.length) {
    const dateFrom = document.getElementById("balanceDateFrom");
    const dateTo = document.getElementById("balanceDateTo");
    dateFrom.min = dates[0];
    dateFrom.max = dates[dates.length - 1];
    dateTo.min = dates[0];
    dateTo.max = dates[dates.length - 1];
  }
}

function renderDashboard() {
  const filteredExpenses = filterExpenses(dashboardState.expenses);
  renderSummary();
  renderChart(filteredExpenses);
  renderDetails(filteredExpenses);
  renderBalance();
}

async function loadDashboard() {
  const [summary, expenses, movements] = await Promise.all([
    api("/api/dashboard/summary"),
    api("/api/expenses?limit=50000"),
    api("/api/expenses?expenses_only=false&limit=50000"),
  ]);
  dashboardState = {
    ...dashboardState,
    lastSync: summary.last_sync,
    googleSheetsUrl: summary.google_sheets_url,
    expenses,
    movements,
  };
  renderFilterOptions();
  renderBalanceFilterOptions();
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
  for (const checkbox of document.querySelectorAll(
    "#accountFilter input[type='checkbox'], #excludedCategoryFilter input[type='checkbox']",
  )) {
    checkbox.checked = false;
  }
  document.getElementById("minCategoryTotal").value = "";
  dashboardState.selectedCategory = null;
  renderFilterOptions();
  renderDashboard();
}

function resetBalanceFilters() {
  document.getElementById("balanceDateFrom").value = "";
  document.getElementById("balanceDateTo").value = "";
  for (const checkbox of document.querySelectorAll(
    "#balanceCurrencyFilter input[type='checkbox'], #accountTypeFilter input[type='checkbox'], #accountStatusFilter input[type='checkbox']",
  )) {
    checkbox.checked = false;
  }
  document.getElementById("convertToEur").checked = false;
  dashboardState.convertToEur = false;
  renderBalanceFilterOptions();
  renderDashboard();
}

function setActiveView(view) {
  dashboardState.activeView = view;
  document.getElementById("pageTitle").textContent = view === "balance" ? "Баланс" : "Расходы";
  document.getElementById("expenseView").classList.toggle("is-hidden", view !== "expenses");
  document.getElementById("balanceView").classList.toggle("is-hidden", view !== "balance");
  for (const [buttonId, buttonView] of [
    ["expensesTab", "expenses"],
    ["balanceTab", "balance"],
  ]) {
    const button = document.getElementById(buttonId);
    const active = view === buttonView;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  renderDashboard();
}

async function handleConvertToEurChange(event) {
  dashboardState.convertToEur = event.target.checked;
  dashboardState.exchangeRateError = null;
  renderBalance();
  if (!dashboardState.convertToEur) return;

  try {
    await ensureExchangeRates();
  } catch (error) {
    dashboardState.exchangeRateError = error.message;
  }
  renderBalance();
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

for (const id of [
  "balanceDateFrom",
  "balanceDateTo",
  "balanceCurrencyFilter",
  "accountTypeFilter",
  "accountStatusFilter",
]) {
  document.getElementById(id).addEventListener("change", renderDashboard);
}

document.getElementById("syncButton").addEventListener("click", syncFromSheets);
document.getElementById("resetFilters").addEventListener("click", resetFilters);
document.getElementById("resetBalanceFilters").addEventListener("click", resetBalanceFilters);
document.getElementById("expensesTab").addEventListener("click", () => setActiveView("expenses"));
document.getElementById("balanceTab").addEventListener("click", () => setActiveView("balance"));
document.getElementById("convertToEur").addEventListener("change", handleConvertToEurChange);
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
