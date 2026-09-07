const assert = require("node:assert/strict");
const test = require("node:test");

const {
  aggregateCashflowSankey,
  aggregateMonthlyCashflow,
  setExchangeRatesForTests,
} = require("../static/app.js");

test("builds income-to-category-to-subcategory totals for the Sankey view", () => {
  setExchangeRatesForTests({ EUR: 1, USD: 2, RSD: 117 });

  const flow = aggregateCashflowSankey(
    [
      { amount: "1000", currency: "EUR", category: "Зарплата" },
      { amount: "400", currency: "USD", category: "Фриланс" },
      { amount: "-23400", currency: "RSD", category: "Жильё", subcategory: "Аренда" },
      { amount: "-100", currency: "EUR", category: "Еда", subcategory: "Продукты" },
      { amount: "-50", currency: "EUR", category: "Еда", subcategory: "Кафе" },
    ],
    "EUR",
  );

  assert.equal(flow.totalIncome, 1200);
  assert.equal(flow.totalExpenses, 350);
  assert.equal(flow.difference, 850);
  assert.deepEqual(flow.incomeSources, [
    { name: "Зарплата", total: 1000 },
    { name: "Фриланс", total: 200 },
  ]);
  assert.deepEqual(flow.expenseCategories, [
    {
      name: "Жильё",
      total: 200,
      subcategories: [{ name: "Аренда", total: 200 }],
    },
    {
      name: "Еда",
      total: 150,
      subcategories: [
        { name: "Продукты", total: 100 },
        { name: "Кафе", total: 50 },
      ],
    },
  ]);
});

test("reports a deficit and ignores zero or unconvertible Sankey movements", () => {
  setExchangeRatesForTests({ EUR: 1 });

  const flow = aggregateCashflowSankey(
    [
      { amount: "100", currency: "EUR", category: "Доход" },
      { amount: "-175", currency: "EUR", category: "Расход" },
      { amount: "0", currency: "EUR", category: "Ноль" },
      { amount: "-50", currency: "XYZ", category: "Без курса" },
    ],
    "EUR",
  );

  assert.equal(flow.totalIncome, 100);
  assert.equal(flow.totalExpenses, 175);
  assert.equal(flow.difference, -75);
});

test("aggregates monthly income and expenses after converting currencies", () => {
  setExchangeRatesForTests({ EUR: 1, USD: 2, RSD: 117 });

  const months = aggregateMonthlyCashflow(
    [
      { date: "2026-01-03", amount: "200", currency: "USD" },
      { date: "2026-01-08", amount: "-11700", currency: "RSD" },
      { date: "2026-02-01", amount: "500", currency: "EUR" },
      { date: "2026-02-20", amount: "-200", currency: "EUR" },
    ],
    "EUR",
  );

  assert.deepEqual(months, [
    { month: "2026-01", income: 100, expenses: 100, difference: 0 },
    { month: "2026-02", income: 500, expenses: 200, difference: 300 },
  ]);
});

test("sorts months chronologically and ignores zero movements", () => {
  setExchangeRatesForTests({ EUR: 1 });

  const months = aggregateMonthlyCashflow(
    [
      { date: "2026-12-10", amount: "-10", currency: "EUR" },
      { date: "2026-01-10", amount: "25", currency: "EUR" },
      { date: "2026-06-10", amount: "0", currency: "EUR" },
    ],
    "EUR",
  );

  assert.deepEqual(months.map((item) => item.month), ["2026-01", "2026-12"]);
});
