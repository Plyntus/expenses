const assert = require("node:assert/strict");
const test = require("node:test");

const { aggregateMonthlyExpenses, setExchangeRatesForTests } = require("../static/app.js");

test("monthly expenses convert currencies and fill gaps across years and period edges", () => {
  setExchangeRatesForTests({ EUR: 1, USD: 2, RSD: 117 });
  const series = aggregateMonthlyExpenses([
    { date: "2026-02-20", amount: "-11700", currency: "RSD", category: "Еда" },
    { date: "2025-12-04", amount: "-200", currency: "USD", category: "Жильё" },
    { date: "2025-12-12", amount: "-50", currency: "EUR", category: "Еда" },
  ], 0, "EUR", { dateFrom: "2025-11-15", dateTo: "2026-03-04" });

  assert.deepEqual(series.months, ["2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]);
  assert.deepEqual(series.totals, [0, 150, 0, 100, 0]);
  assert.deepEqual(series.categories, [
    { category: "Еда", totals: [0, 50, 0, 100, 0] },
    { category: "Жильё", totals: [0, 100, 0, 0, 0] },
  ]);
});

test("category threshold groups small categories over the entire selected period without changing totals", () => {
  setExchangeRatesForTests({ EUR: 1 });
  const expenses = [
    { date: "2026-01-01", amount: "-80", currency: "EUR", category: "Еда" },
    { date: "2026-02-01", amount: "-80", currency: "EUR", category: "Еда" },
    { date: "2026-01-02", amount: "-20", currency: "EUR", category: "Кафе" },
    { date: "2026-02-02", amount: "-30", currency: "EUR", category: "Такси" },
    { date: "2026-02-03", amount: "-10", currency: "EUR", category: "Другое" },
  ];
  const series = aggregateMonthlyExpenses(expenses, 100, "EUR");

  assert.deepEqual(series.categories, [
    { category: "Еда", totals: [80, 80] },
    { category: "Другое", totals: [20, 40] },
  ]);
  assert.deepEqual(series.totals, aggregateMonthlyExpenses(expenses, 0, "EUR").totals);
  series.totals.forEach((total, index) => {
    assert.equal(total, series.categories.reduce((sum, item) => sum + item.totals[index], 0));
  });
});

test("supports one month and open-ended periods", () => {
  setExchangeRatesForTests({ EUR: 1 });
  const expenses = [{ date: "2026-02-15", amount: "-25", currency: "EUR", category: null }];
  assert.deepEqual(aggregateMonthlyExpenses(expenses, 0, "EUR"), {
    months: ["2026-02"], totals: [25], categories: [{ category: "Без категории", totals: [25] }],
  });
  assert.deepEqual(aggregateMonthlyExpenses(expenses, 0, "EUR", { dateFrom: "2026-01-01" }).totals, [0, 25]);
  assert.deepEqual(aggregateMonthlyExpenses(expenses, 0, "EUR", { dateTo: "2026-03-31" }).totals, [25, 0]);
});

test("empty or unusable expenses do not produce a misleading zero series", () => {
  setExchangeRatesForTests({ EUR: 1 });
  const empty = { months: [], totals: [], categories: [] };
  assert.deepEqual(aggregateMonthlyExpenses([], 0, "EUR"), empty);
  assert.deepEqual(aggregateMonthlyExpenses([
    { date: "2026-01-01", amount: "0", currency: "EUR" },
    { date: "2026-02-01", amount: "-10", currency: "XYZ" },
    { date: null, amount: "-20", currency: "EUR" },
    { date: "2026-13-01", amount: "-30", currency: "EUR" },
  ], 0, "EUR"), empty);
});
