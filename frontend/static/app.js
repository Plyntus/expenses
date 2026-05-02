const moneyFormatter = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

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

function renderMetrics(summary) {
  const lastSync = summary.last_sync;
  const firstCurrency = summary.latest_expenses?.[0]?.currency || "";
  document.getElementById("totalSpending").textContent = formatMoney(
    summary.total_spending,
    firstCurrency,
  );
  document.getElementById("summaryText").textContent =
    `${summary.latest_expenses.length} latest expenses loaded from Postgres`;
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
  if (summary.google_sheets_url) {
    sheetLink.href = summary.google_sheets_url;
    sheetLink.hidden = false;
  } else {
    sheetLink.hidden = true;
  }
}

function renderCharts(summary) {
  const categories = summary.spending_by_category || [];
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

  const months = summary.spending_by_month || [];
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
  for (const expense of expenses || []) {
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
  renderMetrics(summary);
  renderCharts(summary);
  renderLatestExpenses(summary.latest_expenses);
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
loadDashboard().catch((error) => {
  const box = document.getElementById("syncError");
  box.hidden = false;
  box.textContent = error.message;
});
