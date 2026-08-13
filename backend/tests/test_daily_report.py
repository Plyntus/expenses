from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.telegram_bot.daily_report import (
    BudgetCategorySpend,
    ExchangeRates,
    _format_budget_category_spending,
    _run_rate,
    build_daily_report_message,
)


class DailyReportRunRateTests(unittest.TestCase):
    def test_run_rate_projects_spending_to_full_month(self) -> None:
        self.assertEqual(
            _run_rate(Decimal("600"), elapsed_days=15, days_in_month=30),
            Decimal("1200"),
        )

    def test_run_rate_supports_leap_year_month(self) -> None:
        self.assertEqual(
            _run_rate(Decimal("290"), elapsed_days=10, days_in_month=29),
            Decimal("841"),
        )

    def test_category_line_includes_run_rate_and_budget_percentage(self) -> None:
        lines = _format_budget_category_spending(
            [
                BudgetCategorySpend(
                    category="Питание",
                    spent=Decimal("600"),
                    budget=Decimal("930"),
                    currency="EUR",
                )
            ],
            elapsed_days=15,
            days_in_month=30,
        )

        self.assertEqual(lines, ["Питание: 600 (65%) RR: 1 200 (129%)"])

    def test_zero_budget_keeps_existing_percentage_semantics(self) -> None:
        lines = _format_budget_category_spending(
            [
                BudgetCategorySpend(
                    category="Другое",
                    spent=Decimal("0"),
                    budget=Decimal("0"),
                    currency="EUR",
                )
            ],
            elapsed_days=10,
            days_in_month=31,
        )

        self.assertEqual(lines, ["Другое: 0 (0%) RR: 0 (0%)"])

    @patch("app.telegram_bot.daily_report._budget_category_spending")
    @patch("app.telegram_bot.daily_report._monthly_spending")
    def test_report_uses_latest_transaction_day_for_run_rate(
        self,
        monthly_spending_mock,
        category_spending_mock,
    ) -> None:
        monthly_spending_mock.return_value = ({}, {}, date(2026, 2, 10))
        category_spending_mock.return_value = [
            BudgetCategorySpend(
                category="Питание",
                spent=Decimal("100"),
                budget=Decimal("280"),
                currency="EUR",
            )
        ]

        message = build_daily_report_message(
            db=object(),
            report_date=date(2026, 2, 14),
            budget_categories=[],
            exchange_rates=ExchangeRates(
                base_currency="EUR",
                rates={"EUR": Decimal("1")},
            ),
            excluded_categories=[],
        )

        self.assertIn("Питание: 100 (36%) RR: 280 (100%)", message)


if __name__ == "__main__":
    unittest.main()
