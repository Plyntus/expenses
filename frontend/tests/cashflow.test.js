const assert = require("node:assert/strict");
const test = require("node:test");

const {
  aggregateMonthlyCashflow,
  setExchangeRatesForTests,
} = require("../static/app.js");

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
