const moneyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

let dashboardState = {
  lastSync: null,
  googleSheetsUrl: null,
  expenses: [],
};

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

function formatMoney(value, currency) {
  const amount = moneyFormatter.format(Math.abs(Number(value || 0)));
  return currency ? `${amount} ${currency}` : amount;
}

function formatDateTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

function getDateFilters() {
  return {
    dateFrom: document.getElementById("dateFrom").value,
    dateTo: document.getElementById("dateTo").value,
  };
}

function filterExpenses(expenses) {
  const { dateFrom, dateTo } = getDateFilters();
  return expenses.filter((expense) => {
    if (dateFrom && expense.date < dateFrom) return false;
    if (dateTo && expense.date > dateTo) return false;
    return true;
  });
}

function summarizeExpenses(expenses) {
  const byCategory = new Map();
  const byMonth = new Map();
  let total = 0;

  for (const expense of expenses) {
    const amount = Math.abs(Number(expense.amount || 0));
    total += amount;

    const category = expense.category || "Без категории";
    byCategory.set(category, (byCategory.get(category) || 0) + amount);

    const month = expense.date.slice(0, 7);
    byMonth.set(month, (byMonth.get(month) || 0) + amount);
  }

  return {
    totalSpending: total,
    spendingByCategory: [...byCategory.entries()]
      .map(([category, categoryTotal]) => ({ category, total: categoryTotal }))
      .sort((left, right) => right.total - left.total),
    spendingByMonth: [...byMonth.entries()]
      .map(([month, monthTotal]) => ({ month, total: monthTotal }))
      .sort((left, right) => left.month.localeCompare(right.month)),
  };
}

function renderMetrics(expenses) {
  const lastSync = dashboardState.lastSync;
  const firstCurrency = expenses?.[0]?.currency || dashboardState.expenses?.[0]?.currency || "";
  const summary = summarizeExpenses(expenses);
  document.getElementById("totalSpending").textContent = formatMoney(
    summary.totalSpending,
    firstCurrency,
  );
  const filters = getDateFilters();
  const period = filters.dateFrom || filters.dateTo
    ? ` · ${filters.dateFrom || "start"} to ${filters.dateTo || "today"}`
    : "";
  document.getElementById("summaryText").textContent =
    `${expenses.length} expenses shown from Postgres${period}`;
  document.getElementById("lastSync").textContent = formatDateTime(lastSync?.finished_at);
  document.getElementById("importedRows").textContent = lastSync?.rows_imported ?? "-";
  document.getElementById("syncStatus").textContent = lastSync?.status ?? "never synced";

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

function renderCharts(expenses) {
  const summary = summarizeExpenses(expenses);
  const categories = summary.spendingByCategory;
  Plotly.newPlot(
    "categoryChart",
    [
      {
        type: "bar",
        orientation: "h",
        y: categories.map((item) => item.category),
        x: categories.map((item) => item.total),
        marker: { color: "#2563eb" },
      },
    ],
    {
      margin: { l: 150, r: 20, t: 10, b: 40 },
      xaxis: { title: "Amount", gridcolor: "#eaf0fb" },
      yaxis: { autorange: "reversed" },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      font: { family: "Avenir Next, Segoe UI, Arial, sans-serif", color: "#0f172a" },
    },
    { displayModeBar: false, responsive: true },
  );

  const months = summary.spendingByMonth;
  Plotly.newPlot(
    "monthChart",
    [
      {
        type: "scatter",
        mode: "lines+markers",
        x: months.map((item) => item.month),
        y: months.map((item) => item.total),
        line: { color: "#0f766e", width: 3 },
        marker: { color: "#0f766e", size: 7 },
      },
    ],
    {
      margin: { l: 55, r: 20, t: 10, b: 45 },
      yaxis: { title: "Amount", gridcolor: "#eaf0fb" },
      paper_bgcolor: "#ffffff",
      plot_bgcolor: "#ffffff",
      font: { family: "Avenir Next, Segoe UI, Arial, sans-serif", color: "#0f172a" },
    },
    { displayModeBar: false, responsive: true },
  );
}

function renderLatestExpenses(expenses) {
  const tbody = document.getElementById("latestRows");
  tbody.innerHTML = "";
  for (const expense of (expenses || []).slice(0, 100)) {
    const tr = document.createElement("tr");
    const values = [
      expense.date,
      expense.payment_method || "",
      expense.category || "",
      expense.subcategory || "",
      formatMoney(expense.amount, ""),
      expense.currency || "",
      expense.comment || "",
    ];
    for (const value of values) {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
}

async function loadDashboard() {
  const summary = await api("/api/dashboard/summary");
  const expenses = await api("/api/expenses?limit=50000");
  dashboardState = {
    lastSync: summary.last_sync,
    googleSheetsUrl: summary.google_sheets_url,
    expenses,
  };
  renderDashboard();
}

function renderDashboard() {
  const expenses = filterExpenses(dashboardState.expenses);
  renderMetrics(expenses);
  renderCharts(expenses);
  renderLatestExpenses(expenses);
}

async function syncFromSheets() {
  const button = document.getElementById("syncButton");
  button.disabled = true;
  button.textContent = "Syncing...";
  try {
    await api("/api/sync/google-sheets", { method: "POST" });
    await loadDashboard();
  } catch (error) {
    const box = document.getElementById("syncError");
    box.hidden = false;
    box.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Sync from Google Sheets";
  }
}

document.getElementById("syncButton").addEventListener("click", syncFromSheets);
document.getElementById("dateFrom").addEventListener("change", renderDashboard);
document.getElementById("dateTo").addEventListener("change", renderDashboard);
document.getElementById("resetDateFilter").addEventListener("click", () => {
  document.getElementById("dateFrom").value = "";
  document.getElementById("dateTo").value = "";
  renderDashboard();
});
loadDashboard().catch((error) => {
  const box = document.getElementById("syncError");
  box.hidden = false;
  box.textContent = error.message;
});
