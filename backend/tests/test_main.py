import unittest

from app.main import ASSET_VERSION, index


class StaticAssetVersionTests(unittest.TestCase):
    def test_index_uses_content_version_for_css_and_javascript(self) -> None:
        response = index()
        html = response.body.decode("utf-8")

        self.assertIn(f'/static/styles.css?v={ASSET_VERSION}', html)
        self.assertIn(f'/static/app.js?v={ASSET_VERSION}', html)
        self.assertNotIn("{{ASSET_VERSION}}", html)

    def test_index_is_not_cached(self) -> None:
        response = index()

        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
