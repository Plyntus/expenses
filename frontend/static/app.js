const OTHER_CATEGORY = "Другое";
const FALLBACK_CATEGORY = "Без категории";
const FALLBACK_SUBCATEGORY = "Без субкатегории";
const FALLBACK_ACCOUNT = "Без счета";
const FALLBACK_ACCOUNT_TYPE = "Без типа";
const FALLBACK_ACCOUNT_STATUS = "Без статуса";
const FALLBACK_INCOME_SOURCE = "Прочие поступления";
const OTHER_INCOME_SOURCE = "Другие поступления";
const OTHER_EXPENSE_CATEGORY = "Другие расходы";
const OTHER_EXPENSE_SUBCATEGORY = "Другие подкатегории";
const EUR_RATE_API_URL = "https://open.er-api.com/v6/latest/EUR";

const moneyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const wholeNumberFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
});

const monthFormatter = new Intl.DateTimeFormat("ru-RU", {
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const percentFormatter = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 1,
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

const cashflowCategoryColors = [
  "#E07A5F",
  "#7C6FCD",
  "#D69E2E",
  "#3D8D9B",
  "#C05A8C",
  "#5B7DB1",
  "#87964B",
  "#A26A45",
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
  exchangeRatesPromise: null,
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

function normalizeCurrency(value) {
  return normalizeText(value).toUpperCase();
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
  const code = normalizeCurrency(currency);
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

function formatPercent(value, total) {
  if (!Number(total)) return "0%";
  return `${percentFormatter.format((Number(value || 0) / Number(total)) * 100)}%`;
}

function formatDate(value) {
  if (!value) return "-";
  const [year, month, day] = String(value).split("-");
  return year && month && day ? `${day}.${month}.${year}` : String(value);
}

function selectedPeriodLabel() {
  const { dateFrom, dateTo } = getFilters();
  if (dateFrom && dateTo) return `${formatDate(dateFrom)} — ${formatDate(dateTo)}`;
  if (dateFrom) return `С ${formatDate(dateFrom)}`;
  if (dateTo) return `До ${formatDate(dateTo)}`;
  return "За всё время";
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("ru-RU");
}

function formatDateInputValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentMonthDateRange() {
  const today = new Date();
  return {
    dateFrom: formatDateInputValue(new Date(today.getFullYear(), today.getMonth(), 1)),
    dateTo: formatDateInputValue(today),
  };
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

    if (filters.dateFrom && expense.date < filters.dateFrom) return false;
    if (filters.dateTo && expense.date > filters.dateTo) return false;
    if (accountSet.size && !accountSet.has(account)) return false;
    if (excludedCategorySet.has(category)) return false;
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
  if (dashboardState.exchangeRatesPromise) return dashboardState.exchangeRatesPromise;

  dashboardState.exchangeRatesPromise = (async () => {
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
  })();

  try {
    return await dashboardState.exchangeRatesPromise;
  } finally {
    dashboardState.exchangeRatesPromise = null;
  }
}

function convertAmountToEur(value, currency) {
  const code = normalizeCurrency(currency);
  if (!code || code === "EUR") return Number(value || 0);
  const rate = dashboardState.exchangeRates?.rates?.[code];
  if (!rate) return null;
  return Number(value || 0) / Number(rate);
}

function convertAmount(value, sourceCurrency, targetCurrency) {
  const source = normalizeCurrency(sourceCurrency);
  const target = normalizeCurrency(targetCurrency);
  if (!source || !target) return null;
  if (source === target) return Number(value || 0);

  const sourceRate = dashboardState.exchangeRates?.rates?.[source];
  const targetRate = dashboardState.exchangeRates?.rates?.[target];
  if (!sourceRate || !targetRate) return null;
  return (Number(value || 0) / Number(sourceRate)) * Number(targetRate);
}

function expenseAmountInCurrency(expense, targetCurrency) {
  return convertAmount(expenseAmount(expense), expense.currency, targetCurrency);
}

function missingRateCurrencies(expenses, targetCurrency) {
  const target = normalizeCurrency(targetCurrency);
  const missing = new Set();
  for (const expense of expenses) {
    if (!expenseAmount(expense)) continue;
    const source = normalizeCurrency(expense.currency);
    if (!source) {
      missing.add("валюта не указана");
      continue;
    }
    if (source !== target && expenseAmountInCurrency(expense, target) == null) {
      missing.add(source);
    }
  }
  return [...missing].sort();
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

function summarizeExpenses(expenses, targetCurrency) {
  let total = 0;
  let convertedCount = 0;
  for (const expense of expenses) {
    const amount = expenseAmountInCurrency(expense, targetCurrency);
    if (amount == null) continue;
    total += amount;
    convertedCount += 1;
  }
  return {
    total,
    count: expenses.length,
    complete: convertedCount === expenses.length,
  };
}

function buildCategoryTotals(expenses, targetCurrency) {
  const totals = new Map();
  for (const expense of expenses) {
    const amount = expenseAmountInCurrency(expense, targetCurrency);
    if (amount == null) continue;
    const category = normalizeText(expense.category, FALLBACK_CATEGORY);
    totals.set(category, (totals.get(category) || 0) + amount);
  }
  return totals;
}

function collapseCategory(category, collapsedCategories) {
  return collapsedCategories.has(category) ? OTHER_CATEGORY : category;
}

function aggregateForChart(expenses, minCategoryTotal, targetCurrency) {
  const categoryTotals = buildCategoryTotals(expenses, targetCurrency);
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
    const amount = expenseAmountInCurrency(expense, targetCurrency);
    if (amount == null) continue;
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

function monthLabel(monthKey) {
  const [year, month] = monthKey.split("-").map(Number);
  if (!year || !month) return monthKey;
  return monthFormatter.format(new Date(Date.UTC(year, month - 1, 1))).replace(" г.", "");
}

function aggregateMonthlyCashflow(movements, targetCurrency) {
  const grouped = new Map();
  for (const movement of movements) {
    const signed = signedAmount(movement);
    if (!signed) continue;
    const converted = convertAmount(Math.abs(signed), movement.currency, targetCurrency);
    if (converted == null) continue;

    const month = String(movement.date || "").slice(0, 7);
    if (!month) continue;
    if (!grouped.has(month)) {
      grouped.set(month, { month, income: 0, expenses: 0, difference: 0 });
    }
    const item = grouped.get(month);
    if (signed > 0) item.income += converted;
    if (signed < 0) item.expenses += converted;
    item.difference = item.income - item.expenses;
  }
  return [...grouped.values()].sort((left, right) => left.month.localeCompare(right.month));
}

function aggregateCashflowSankey(movements, targetCurrency) {
  const incomeSources = new Map();
  const expenseCategories = new Map();
  let totalIncome = 0;
  let totalExpenses = 0;

  for (const movement of movements) {
    const signed = signedAmount(movement);
    if (!signed) continue;
    const converted = convertAmount(Math.abs(signed), movement.currency, targetCurrency);
    if (converted == null) continue;

    if (signed > 0) {
      const source = normalizeText(movement.category, FALLBACK_INCOME_SOURCE);
      incomeSources.set(source, (incomeSources.get(source) || 0) + converted);
      totalIncome += converted;
      continue;
    }

    const category = normalizeText(movement.category, FALLBACK_CATEGORY);
    const subcategory = normalizeText(movement.subcategory, FALLBACK_SUBCATEGORY);
    if (!expenseCategories.has(category)) {
      expenseCategories.set(category, { name: category, total: 0, subcategories: new Map() });
    }
    const group = expenseCategories.get(category);
    group.total += converted;
    group.subcategories.set(
      subcategory,
      (group.subcategories.get(subcategory) || 0) + converted,
    );
    totalExpenses += converted;
  }

  const byTotalThenName = (left, right) =>
    right.total - left.total || left.name.localeCompare(right.name, "ru");
  return {
    totalIncome,
    totalExpenses,
    difference: totalIncome - totalExpenses,
    incomeSources: [...incomeSources.entries()]
      .map(([name, total]) => ({ name, total }))
      .sort(byTotalThenName),
    expenseCategories: [...expenseCategories.values()]
      .map((category) => ({
        name: category.name,
        total: category.total,
        subcategories: [...category.subcategories.entries()]
          .map(([name, total]) => ({ name, total }))
          .sort(byTotalThenName),
      }))
      .sort(byTotalThenName),
  };
}

function collapseCashflowItems(items, limit, otherName) {
  if (items.length <= limit) return items;
  const visibleCount = Math.max(1, limit - 1);
  const visible = items.slice(0, visibleCount);
  const hidden = items.slice(visibleCount);
  const subcategoryTotals = new Map();
  for (const item of hidden) {
    for (const subcategory of item.subcategories || []) {
      subcategoryTotals.set(
        subcategory.name,
        (subcategoryTotals.get(subcategory.name) || 0) + subcategory.total,
      );
    }
  }
  return [
    ...visible,
    {
      name: otherName,
      total: hidden.reduce((sum, item) => sum + item.total, 0),
      subcategories: [...subcategoryTotals.entries()]
        .map(([name, total]) => ({ name, total }))
        .sort((left, right) => right.total - left.total),
    },
  ];
}

function getDisplayCurrency() {
  return normalizeCurrency(document.getElementById("currencyFilter").value);
}

function expenseConversionStatus(expenses, targetCurrency) {
  const missing = missingRateCurrencies(expenses, targetCurrency);
  if (!missing.length) return { ready: true, message: "" };
  if (dashboardState.exchangeRateError) {
    return { ready: false, message: dashboardState.exchangeRateError };
  }
  if (!dashboardState.exchangeRates) {
    return { ready: false, message: "Загрузка курсов валют..." };
  }
  return { ready: false, message: `Нет курса для: ${missing.join(", ")}` };
}

function renderExpenseRateMeta(filteredExpenses) {
  const meta = document.getElementById("expenseRateMeta");
  const targetCurrency = getDisplayCurrency();
  const status = expenseConversionStatus(filteredExpenses, targetCurrency);
  const sourceCurrencies = new Set(
    filteredExpenses.map((expense) => normalizeCurrency(expense.currency)).filter(Boolean),
  );
  const needsConversion =
    !status.ready || [...sourceCurrencies].some((currency) => currency !== targetCurrency);
  if (!needsConversion) {
    meta.hidden = true;
    meta.textContent = "";
    meta.classList.remove("is-error");
    return;
  }

  meta.hidden = false;
  meta.classList.toggle(
    "is-error",
    !status.ready && Boolean(dashboardState.exchangeRateError || dashboardState.exchangeRates),
  );
  if (!status.ready) {
    meta.textContent = status.message;
    return;
  }
  const updated = dashboardState.exchangeRates?.updated
    ? ` · обновлено: ${dashboardState.exchangeRates.updated}`
    : "";
  meta.textContent = `Все суммы пересчитаны в ${targetCurrency} по курсам open.er-api.com${updated}`;
}

function renderSummary(filteredExpenses, filteredMovements) {
  const summary = document.getElementById("overallSummary");
  summary.classList.toggle("is-hidden", dashboardState.activeView === "balance");
  const currency = getDisplayCurrency();
  if (dashboardState.activeView === "cashflow") {
    const status = expenseConversionStatus(filteredMovements, currency);
    const flow = status.ready
      ? aggregateCashflowSankey(filteredMovements, currency)
      : { totalIncome: 0, totalExpenses: 0, difference: 0 };
    summary.textContent = status.ready
      ? `Доходы: ${formatMoney(flow.totalIncome, currency)} · Расходы: ${formatMoney(flow.totalExpenses, currency)} · Итог: ${formatMoney(flow.difference, currency, { signed: true })}`
      : "Доходы и расходы: —";
    renderExpenseRateMeta(filteredMovements);
  } else {
    const overall = summarizeExpenses(filteredExpenses, currency);
    summary.textContent =
      `Всего: ${overall.complete ? formatMoney(overall.total, currency) : "—"} · ${overall.count} транзакций`;
    renderExpenseRateMeta(filteredExpenses);
  }
  document.getElementById("minCategoryTotalLabel").textContent =
    `Мин. сумма категории, ${currency}`;

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
  const currency = getDisplayCurrency();
  const chartElement = document.getElementById("categoryChart");
  const chartWidth = chartElement.clientWidth || window.innerWidth;
  const compactChart = chartWidth < 1180;
  const conversionStatus = expenseConversionStatus(filteredExpenses, currency);
  const aggregated = conversionStatus.ready
    ? aggregateForChart(filteredExpenses, filters.minCategoryTotal, currency)
    : { categories: [], subcategories: [], collapsedCategories: new Set() };
  const { categories, subcategories, collapsedCategories } = aggregated;
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
          text: conversionStatus.ready
            ? "Нет транзакций для выбранных фильтров"
            : conversionStatus.message,
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
        title: `Сумма, ${currency}`,
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

function renderCashflowTable(flow, currency) {
  const tbody = document.getElementById("cashflowTableRows");
  tbody.innerHTML = "";
  const rows = [
    ...flow.incomeSources.map((item) => [
      "Источник дохода",
      item.name,
      item.total,
      formatPercent(item.total, flow.totalIncome),
    ]),
    ...flow.expenseCategories.map((item) => [
      "Категория расходов",
      item.name,
      item.total,
      formatPercent(item.total, flow.totalExpenses),
    ]),
    [
      flow.difference >= 0 ? "Остаток" : "Дефицит",
      "Итог периода",
      Math.abs(flow.difference),
      formatPercent(Math.abs(flow.difference), flow.totalIncome),
    ],
  ];
  for (const [type, name, amount, share] of rows) {
    const row = document.createElement("tr");
    for (const value of [type, name, formatMoney(amount, currency), share]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    }
    tbody.appendChild(row);
  }
}

function escapeChartText(value) {
  return String(value).replace(
    /[&<>"']/g,
    (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character],
  );
}

function cashflowNodeLabel(name, value, shareTotal, currency, compact) {
  const maxLength = compact ? 17 : 26;
  const visibleName = name.length > maxLength ? `${name.slice(0, maxLength - 1)}…` : name;
  return `${escapeChartText(visibleName)}<br>${formatWholeMoney(value, currency)} · ${formatPercent(value, shareTotal)}`;
}

function hexToRgba(hex, alpha) {
  const value = hex.replace("#", "");
  const number = Number.parseInt(value, 16);
  const red = (number >> 16) & 255;
  const green = (number >> 8) & 255;
  const blue = number & 255;
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function buildCashflowSankeyTrace(flow, currency, chartWidth) {
  const compact = chartWidth < 700;
  const showSubcategories = chartWidth >= 1040;
  const showIncomeSources = chartWidth >= 760;
  const categoryLimit = showSubcategories ? 8 : compact ? 5 : 7;
  const categories = collapseCashflowItems(
    flow.expenseCategories,
    categoryLimit,
    OTHER_EXPENSE_CATEGORY,
  );
  const sources = showIncomeSources
    ? collapseCashflowItems(flow.incomeSources, 5, OTHER_INCOME_SOURCE)
    : flow.totalIncome > 0
      ? [{ name: "Поступления", total: flow.totalIncome }]
      : [];
  const deficit = Math.max(0, -flow.difference);
  const surplus = Math.max(0, flow.difference);
  const distributedTotal = Math.max(flow.totalIncome, flow.totalExpenses);
  const categoryX = showSubcategories ? 0.56 : 0.96;

  const nodes = [];
  const links = [];
  function addNode(name, value, shareTotal, color, x) {
    const index = nodes.length;
    nodes.push({
      name,
      value,
      color,
      x,
      label: cashflowNodeLabel(name, value, shareTotal, currency, compact),
      hover: `${escapeChartText(name)}<br>${formatMoney(value, currency)}<br>${formatPercent(value, shareTotal)}`,
    });
    return index;
  }
  function addLink(source, target, value, color) {
    links.push({
      source,
      target,
      value,
      color,
      hover: `${escapeChartText(nodes[source].name)} → ${escapeChartText(nodes[target].name)}<br>${formatMoney(value, currency)}`,
    });
  }

  const sourceIndexes = sources.map((source) => ({
    item: source,
    index: addNode(source.name, source.total, flow.totalIncome, "#2AA8B8", 0.01),
  }));
  const deficitIndex = deficit > 0
    ? addNode("Дефицит периода", deficit, distributedTotal, "#C65D4B", 0.01)
    : null;
  const hubName = deficit > 0 ? "Распределено" : "Доходы";
  const hubIndex = addNode(hubName, distributedTotal, distributedTotal, "#168AAD", 0.26);

  for (const source of sourceIndexes) {
    addLink(source.index, hubIndex, source.item.total, hexToRgba("#2AA8B8", 0.24));
  }
  if (deficitIndex != null) {
    addLink(deficitIndex, hubIndex, deficit, hexToRgba("#C65D4B", 0.3));
  }

  categories.forEach((category, categoryIndex) => {
    const color = cashflowCategoryColors[categoryIndex % cashflowCategoryColors.length];
    const categoryNodeIndex = addNode(
      category.name,
      category.total,
      distributedTotal,
      color,
      categoryX,
    );
    addLink(hubIndex, categoryNodeIndex, category.total, hexToRgba(color, 0.25));

    if (!showSubcategories) return;
    const subcategories = collapseCashflowItems(
      category.subcategories || [],
      3,
      OTHER_EXPENSE_SUBCATEGORY,
    );
    for (const subcategory of subcategories) {
      const subcategoryNodeIndex = addNode(
        subcategory.name,
        subcategory.total,
        distributedTotal,
        color,
        0.99,
      );
      addLink(
        categoryNodeIndex,
        subcategoryNodeIndex,
        subcategory.total,
        hexToRgba(color, 0.19),
      );
    }
  });

  if (surplus > 0) {
    const resultIndex = addNode("Остаток", surplus, flow.totalIncome, "#3FAE75", categoryX);
    addLink(hubIndex, resultIndex, surplus, hexToRgba("#3FAE75", 0.27));
  }

  return {
    type: "sankey",
    orientation: "h",
    arrangement: "snap",
    valueformat: ",.2f",
    valuesuffix: ` ${currency}`,
    node: {
      pad: compact ? 18 : 24,
      thickness: compact ? 12 : 15,
      line: { color: "rgba(255, 255, 255, 0.92)", width: 1.5 },
      label: nodes.map((node) => node.label),
      customdata: nodes.map((node) => node.hover),
      hovertemplate: "%{customdata}<extra></extra>",
      color: nodes.map((node) => node.color),
      x: nodes.map((node) => node.x),
    },
    link: {
      source: links.map((link) => link.source),
      target: links.map((link) => link.target),
      value: links.map((link) => link.value),
      color: links.map((link) => link.color),
      customdata: links.map((link) => link.hover),
      hovertemplate: "%{customdata}<extra></extra>",
    },
  };
}

function renderCashflowMetrics(flow, currency, ready) {
  document.getElementById("cashflowPeriod").textContent = selectedPeriodLabel();
  document.getElementById("cashflowIncome").textContent = ready
    ? formatMoney(flow.totalIncome, currency)
    : "—";
  document.getElementById("cashflowExpenses").textContent = ready
    ? formatMoney(flow.totalExpenses, currency)
    : "—";

  const isDeficit = flow.difference < 0;
  const resultCard = document.getElementById("cashflowResultCard");
  document.getElementById("cashflowResultLabel").textContent = isDeficit ? "Дефицит" : "Остаток";
  document.getElementById("cashflowResult").textContent = ready
    ? formatMoney(Math.abs(flow.difference), currency)
    : "—";
  resultCard.classList.toggle("is-deficit", ready && isDeficit);
}

function renderCashflowSankey(filteredMovements) {
  const currency = getDisplayCurrency();
  const chartElement = document.getElementById("cashflowChart");
  const chartWidth = chartElement.clientWidth || window.innerWidth;
  const conversionStatus = expenseConversionStatus(filteredMovements, currency);
  const flow = conversionStatus.ready
    ? aggregateCashflowSankey(filteredMovements, currency)
    : {
        totalIncome: 0,
        totalExpenses: 0,
        difference: 0,
        incomeSources: [],
        expenseCategories: [],
      };
  const hasData = flow.totalIncome > 0 || flow.totalExpenses > 0;

  renderCashflowTable(flow, currency);
  renderCashflowMetrics(flow, currency, conversionStatus.ready);
  chartElement.setAttribute(
    "aria-label",
    hasData
      ? `Распределение денежных потоков в ${currency}. Доходы ${formatMoney(flow.totalIncome, currency)}, расходы ${formatMoney(flow.totalExpenses, currency)}, ${flow.difference >= 0 ? "остаток" : "дефицит"} ${formatMoney(Math.abs(flow.difference), currency)}`
      : conversionStatus.ready
        ? "Нет движений для выбранных фильтров"
        : conversionStatus.message,
  );

  const annotations = hasData
    ? []
    : [
        {
          text: conversionStatus.ready
            ? "Нет движений для выбранных фильтров"
            : conversionStatus.message,
          x: 0.5,
          y: 0.5,
          xref: "paper",
          yref: "paper",
          showarrow: false,
          font: { size: 16, color: "#66758a" },
        },
      ];
  const traces = hasData ? [buildCashflowSankeyTrace(flow, currency, chartWidth)] : [];
  const visibleCategoryCount = Math.min(flow.expenseCategories.length, chartWidth < 700 ? 5 : 8);
  const chartHeight = chartWidth >= 1040
    ? Math.max(520, Math.min(820, visibleCategoryCount * 76 + 180))
    : Math.max(440, Math.min(640, visibleCategoryCount * 62 + 170));

  Plotly.react(
    "cashflowChart",
    traces,
    {
      height: chartHeight,
      showlegend: false,
      margin: {
        l: chartWidth < 700 ? 8 : 22,
        r: chartWidth < 700 ? 8 : 24,
        t: 18,
        b: 18,
      },
      annotations,
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      hoverlabel: {
        bgcolor: "#ffffff",
        bordercolor: "#94a3b8",
        font: { color: "#111827" },
      },
      font: {
        family: "Avenir Next, Segoe UI, Arial, sans-serif",
        color: "#1e293b",
        size: chartWidth < 700 ? 11 : 13,
      },
    },
    { displayModeBar: false, responsive: true },
  );
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
    ...new Set([
      "EUR",
      ...expenses.map((expense) => normalizeCurrency(expense.currency)).filter(Boolean),
    ]),
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
  const dateFrom = document.getElementById("dateFrom");
  const dateTo = document.getElementById("dateTo");
  const currentRange = currentMonthDateRange();
  const earliestAllowed = dates.length
    ? [dates[0], currentRange.dateFrom].sort()[0]
    : currentRange.dateFrom;
  const latestAllowed = dates.length
    ? [dates[dates.length - 1], currentRange.dateTo].sort().at(-1)
    : currentRange.dateTo;
  dateFrom.min = earliestAllowed;
  dateFrom.max = latestAllowed;
  dateTo.min = earliestAllowed;
  dateTo.max = latestAllowed;
  if (!dateFrom.value) dateFrom.value = currentRange.dateFrom;
  if (!dateTo.value) dateTo.value = currentRange.dateTo;
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
  const filteredMovements = filterExpenses(dashboardState.movements);
  renderSummary(filteredExpenses, filteredMovements);
  if (dashboardState.activeView === "expenses") {
    renderChart(filteredExpenses);
    renderDetails(filteredExpenses);
  }
  if (dashboardState.activeView === "cashflow") {
    renderCashflowSankey(filteredMovements);
  }
  if (dashboardState.activeView === "balance") {
    renderBalance();
  }
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

  dashboardState.exchangeRateError = null;
  try {
    await ensureExchangeRates();
  } catch (error) {
    dashboardState.exchangeRateError = error.message;
  }
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
  const titles = {
    expenses: "Расходы",
    cashflow: "Доходы и расходы",
    balance: "Баланс",
  };
  document.getElementById("pageTitle").textContent = titles[view] || titles.expenses;
  document.getElementById("expenseFilters").classList.toggle("is-hidden", view === "balance");
  document.getElementById("minCategoryTotalFilter").classList.toggle(
    "is-hidden",
    view !== "expenses",
  );
  document.getElementById("expenseView").classList.toggle("is-hidden", view !== "expenses");
  document.getElementById("cashflowView").classList.toggle("is-hidden", view !== "cashflow");
  document.getElementById("balanceView").classList.toggle("is-hidden", view !== "balance");
  for (const [buttonId, buttonView] of [
    ["expensesTab", "expenses"],
    ["cashflowTab", "cashflow"],
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

async function handleDisplayCurrencyChange() {
  dashboardState.exchangeRateError = null;
  renderDashboard();

  const rows = dashboardState.activeView === "cashflow"
    ? filterExpenses(dashboardState.movements)
    : filterExpenses(dashboardState.expenses);
  if (expenseConversionStatus(rows, getDisplayCurrency()).ready) return;

  try {
    await ensureExchangeRates();
  } catch (error) {
    dashboardState.exchangeRateError = error.message;
  }
  renderDashboard();
}

if (typeof document !== "undefined") {
  for (const id of [
    "dateFrom",
    "dateTo",
    "accountFilter",
    "excludedCategoryFilter",
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
  document.getElementById("cashflowTab").addEventListener("click", () => setActiveView("cashflow"));
  document.getElementById("balanceTab").addEventListener("click", () => setActiveView("balance"));
  document.getElementById("convertToEur").addEventListener("change", handleConvertToEurChange);
  document.getElementById("currencyFilter").addEventListener("change", handleDisplayCurrencyChange);
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
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    aggregateCashflowSankey,
    aggregateMonthlyCashflow,
    setExchangeRatesForTests(rates) {
      dashboardState.exchangeRates = { base: "EUR", rates };
    },
  };
}
