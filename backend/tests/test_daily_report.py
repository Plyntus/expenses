from datetime import date
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

from app.core.config import TELEGRAM_REPORT_EXCLUDED_CATEGORIES

from app.telegram_bot.daily_report import (
    BudgetCategorySpend,
    ExchangeRates,
    _format_budget_category_spending,
    build_daily_report_message,
    parse_budget_sheet,
)


class DailyReportBudgetTests(unittest.TestCase):
    def test_default_exclusions_remove_business_and_transfers_from_entire_report(self) -> None:
        db = Mock()
        db.execute.return_value.all.return_value = [
            ("EUR", "Питание", Decimal("-100"), date(2026, 10, 1)),
            ("EUR", "Прочее", Decimal("-25"), date(2026, 10, 2)),
            ("EUR", "  бизнес   ДАШИ ", Decimal("-900"), date(2026, 10, 20)),
            ("EUR", "Перевод", Decimal("-500"), date(2026, 10, 21)),
            ("EUR", "Переводы", Decimal("-500"), date(2026, 10, 22)),
        ]
        message = build_daily_report_message(
            db=db,
            report_date=date(2026, 10, 23),
            budget_categories=parse_budget_sheet([
                ["Category", "Budget", "Currency"],
                ["Питание", "200", "EUR"],
                ["Бизнес Даши", "1000", "EUR"],
                ["Остальное", "100", "EUR"],
            ]),
            exchange_rates=ExchangeRates("EUR", {"EUR": Decimal("1")}),
            excluded_categories=TELEGRAM_REPORT_EXCLUDED_CATEGORIES,
        )

        self.assertIn("потрачено 125 EUR. Последняя дата: 2 октября.", message)
        self.assertIn("Питание: 100 (50%)", message)
        self.assertIn("Остальное: 25 (25%)", message)
        self.assertNotIn("Бизнес Даши", message)
        self.assertNotIn("Перевод", message)

    def test_category_line_includes_budget_amount_currency_and_percentage(self) -> None:
        lines = _format_budget_category_spending(
            [
                BudgetCategorySpend(
                    category="Питание",
                    spent=Decimal("893.09"),
                    budget=Decimal("1100"),
                    currency="EUR",
                )
            ],
        )

        self.assertEqual(lines, ["Питание: 893.09 (81%) Budget: 1 100 EUR (81%)"])

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
        )

        self.assertEqual(lines, ["Другое: 0 (0%) Budget: 0 EUR (0%)"])

    @patch("app.telegram_bot.daily_report._budget_category_spending")
    @patch("app.telegram_bot.daily_report._monthly_spending")
    def test_report_includes_latest_transaction_date_and_budget(
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

        self.assertIn("Последняя дата: 10 февраля.", message)
        self.assertIn("Питание: 100 (36%) Budget: 280 EUR (36%)", message)


if __name__ == "__main__":
    unittest.main()
