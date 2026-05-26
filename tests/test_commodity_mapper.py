import sqlite3
import unittest

from portfolio_news_agent.commodity_mapper import build_commodity_exposures
from portfolio_news_agent.storage import create_portfolio_import, insert_asset, migrate


class CommodityMapperTests(unittest.TestCase):
    def test_maps_sample_gold_silver_oil_and_copper_exposures(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        self._asset(connection, import_id, "AEM", "Agnico Eagle Mines", industry="Gold")
        self._asset(connection, import_id, "NEM", "Newmont", industry="Gold")
        self._asset(connection, import_id, "WPM", "Wheaton Precious Metals", industry="Royalty")
        self._asset(connection, import_id, "SILJ", "Junior Silver Miners ETF")
        self._asset(connection, import_id, "PAAS", "Pan American Silver")
        self._asset(connection, import_id, "CVX", "Chevron", sector="Energy")
        self._asset(connection, import_id, "PR", "Permian Resources", industry="Oil and Gas")
        self._asset(connection, import_id, "XLE", "Energy Select Sector SPDR Fund")
        self._asset(connection, import_id, "ERO", "Ero Copper")

        exposures = build_commodity_exposures(connection, import_id)

        self.assertCountEqual(
            self._symbols(exposures["gold_precious_metals"]),
            ["AEM", "NEM", "WPM", "SILJ"],
        )
        self.assertCountEqual(self._symbols(exposures["silver"]), ["SILJ", "PAAS", "WPM"])
        self.assertCountEqual(self._symbols(exposures["oil_energy"]), ["CVX", "PR", "XLE"])
        self.assertCountEqual(self._symbols(exposures["copper"]), ["ERO"])
        self.assertIn("industry: Gold", exposures["gold_precious_metals"][0]["reasons"])

    def test_uses_only_assets_from_current_import(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        old_import_id = create_portfolio_import(
            connection,
            source_path="portfolio-old.csv",
            source_hash="hash-old",
        )
        current_import_id = create_portfolio_import(
            connection,
            source_path="portfolio-current.csv",
            source_hash="hash-current",
        )
        self._asset(connection, old_import_id, "CVX", "Chevron", sector="Energy")
        self._asset(connection, current_import_id, "AAPL", "Apple", sector="Technology")

        exposures = build_commodity_exposures(connection, current_import_id)

        self.assertEqual(self._symbols(exposures["oil_energy"]), [])

    def test_config_overrides_add_symbols_with_explainable_reason(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        self._asset(connection, import_id, "B", "Barrick Gold")

        exposures = build_commodity_exposures(
            connection,
            import_id,
            overrides={"gold_precious_metals": ["B"]},
        )

        self.assertEqual(self._symbols(exposures["gold_precious_metals"]), ["B"])
        self.assertIn("config override", exposures["gold_precious_metals"][0]["reasons"])

    def test_no_false_mapping_when_portfolio_lacks_matching_assets(self):
        connection = sqlite3.connect(":memory:")
        migrate(connection)
        import_id = create_portfolio_import(
            connection,
            source_path="portfolio.csv",
            source_hash="hash-1",
        )
        self._asset(connection, import_id, "AAPL", "Apple", sector="Technology")
        self._asset(connection, import_id, "MSFT", "Microsoft", sector="Technology")

        exposures = build_commodity_exposures(connection, import_id)

        self.assertTrue(all(not candidates for candidates in exposures.values()))

    def _asset(
        self,
        connection: sqlite3.Connection,
        import_id: int,
        symbol: str,
        name: str,
        sector: str | None = None,
        industry: str | None = None,
    ) -> None:
        insert_asset(
            connection,
            import_id=import_id,
            symbol=symbol,
            name=name,
            sector=sector,
            industry=industry,
        )

    def _symbols(self, candidates: list[dict[str, object]]) -> list[str]:
        return [str(candidate["symbol"]) for candidate in candidates]
