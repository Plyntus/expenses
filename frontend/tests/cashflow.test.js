const assert = require("node:assert/strict");
const test = require("node:test");

const {
  aggregateCashflowSankey,
  aggregateMonthlyCashflow,
  buildCashflowSankeyTrace,
  cashflowIncomeTransactions,
  matchesSelectedCategories,
  setExchangeRatesForTests,
} = require("../static/app.js");

test("income details contain only positive movements from the selected sources, newest first", () => {
  const movements = [
    { date: "2026-10-01", amount: "100", category: "Зарплата" },
    { date: "2026-10-02", amount: "-25", category: "Зарплата" },
    { date: "2026-10-03", amount: "0", category: "Зарплата" },
    { date: "2026-10-04", amount: "50", category: " Фриланс " },
    { date: "2026-10-05", amount: "200", category: "Зарплата" },
    { date: "2026-10-06", amount: "10", category: null },
  ];
  assert.deepEqual(cashflowIncomeTransactions(movements, []).map((row) => row.date), [
    "2026-10-06", "2026-10-05", "2026-10-04", "2026-10-01",
  ]);
  assert.deepEqual(cashflowIncomeTransactions(movements, ["Зарплата", "Фриланс"]), [
    movements[4], movements[3], movements[0],
  ]);
  assert.deepEqual(cashflowIncomeTransactions(movements, ["Прочие поступления"]), [movements[5]]);
  assert.deepEqual(cashflowIncomeTransactions([], []), []);
  assert.equal(movements[0].date, "2026-10-01");
});

test("Sankey income nodes and links retain their exact sources when grouped or resized", () => {
  setExchangeRatesForTests({ EUR: 1 });
  const flow = aggregateCashflowSankey([
    ...Array.from({ length: 7 }, (_, index) => ({
      amount: String(700 - index * 100), currency: "EUR", category: `Источник ${index}`,
    })),
    { amount: "-4000", currency: "EUR", category: "Покупки" },
  ], "EUR");
  const desktop = buildCashflowSankeyTrace(flow, "EUR", 1200);
  const nodeSources = desktop.node.customdata.map((data) => data[1]);
  assert.deepEqual(nodeSources.slice(0, 5), [
    ["Источник 0"], ["Источник 1"], ["Источник 2"], ["Источник 3"],
    ["Источник 4", "Источник 5", "Источник 6"],
  ]);
  assert.equal(nodeSources[5], null); // Deficit is not an incoming transaction.
  assert.deepEqual(nodeSources[6], []); // The hub opens all income.
  assert.equal(nodeSources[7], null); // Expenses do not open income details.
  assert.deepEqual(desktop.link.customdata.slice(0, 5).map((data) => data[1]), nodeSources.slice(0, 5));
  assert.ok(desktop.link.customdata.slice(5).every((data) => data[1] === null));

  const mobile = buildCashflowSankeyTrace(flow, "EUR", 390);
  assert.deepEqual(mobile.node.customdata[0][1], []);
  assert.deepEqual(mobile.link.customdata[0][1], []);
});

test("strictly filters income and expenses by selected categories", () => {
  const selectedCategories = new Set(["Жильё"]);

  assert.equal(
    matchesSelectedCategories({ amount: "-100", category: "Жильё" }, selectedCategories),
    true,
  );
  assert.equal(
    matchesSelectedCategories({ amount: "-50", category: "Питание" }, selectedCategories),
    false,
  );
  assert.equal(
    matchesSelectedCategories({ amount: "1000", category: "Зарплата" }, selectedCategories),
    false,
  );
  assert.equal(
    matchesSelectedCategories({ amount: "1000", category: "Жильё" }, selectedCategories),
    true,
  );
  assert.equal(
    matchesSelectedCategories({ amount: "-50", category: "Питание" }, new Set()),
    true,
  );
});

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
