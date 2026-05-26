from __future__ import annotations

import sqlite3
from typing import Any


COMMODITY_THEMES = (
    "gold_precious_metals",
    "silver",
    "oil_energy",
    "copper",
)

_THEME_KEYWORDS = {
    "gold_precious_metals": (
        "gold",
        "precious metal",
        "precious metals",
        "mining",
        "miner",
        "miners",
        "royalty",
        "streaming",
    ),
    "silver": ("silver",),
    "oil_energy": (
        "oil",
        "crude",
        "energy",
        "gas",
        "e&p",
        "exploration",
        "production",
        "permian",
    ),
    "copper": ("copper",),
}

_THEME_SYMBOL_HINTS = {
    "gold_precious_metals": {"AEM", "NEM", "WPM", "SILJ"},
    "silver": {"PAAS", "SILJ", "WPM"},
    "oil_energy": {"CVX", "PR", "XLE"},
    "copper": {"ERO"},
}


def build_commodity_exposures(
    connection: sqlite3.Connection,
    portfolio_import_id: int,
    overrides: dict[str, list[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    assets = _load_assets(connection, portfolio_import_id)
    overrides = _normalize_overrides(overrides or {})
    exposures = {theme: [] for theme in COMMODITY_THEMES}

    for theme in COMMODITY_THEMES:
        override_symbols = overrides.get(theme, set())
        for asset in assets:
            reasons = _matching_reasons(asset, theme)
            if asset["symbol"] in override_symbols:
                reasons.append("config override")
            if reasons:
                exposures[theme].append(
                    {
                        "symbol": asset["symbol"],
                        "name": asset["name"],
                        "sector": asset["sector"],
                        "industry": asset["industry"],
                        "asset_type": asset["asset_type"],
                        "reasons": reasons,
                    }
                )

    return exposures


def _load_assets(
    connection: sqlite3.Connection,
    portfolio_import_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT symbol, name, sector, industry, asset_type
        FROM assets
        WHERE import_id = ?
        ORDER BY id
        """,
        (portfolio_import_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _matching_reasons(asset: dict[str, Any], theme: str) -> list[str]:
    reasons: list[str] = []
    if asset["symbol"] in _THEME_SYMBOL_HINTS[theme]:
        reasons.append(f"symbol hint: {asset['symbol']}")
    for field in ("name", "sector", "industry", "asset_type"):
        value = asset.get(field)
        if not value:
            continue
        matched_keyword = _first_keyword_match(str(value), _THEME_KEYWORDS[theme])
        if matched_keyword:
            reasons.append(f"{field}: {value}")
    return reasons


def _first_keyword_match(value: str, keywords: tuple[str, ...]) -> str | None:
    normalized = value.lower()
    for keyword in keywords:
        if keyword in normalized:
            return keyword
    return None


def _normalize_overrides(overrides: dict[str, list[str]]) -> dict[str, set[str]]:
    normalized: dict[str, set[str]] = {}
    for theme, symbols in overrides.items():
        normalized_theme = _normalize_theme(theme)
        if normalized_theme not in COMMODITY_THEMES:
            continue
        normalized[normalized_theme] = {
            str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()
        }
    return normalized


def _normalize_theme(theme: str) -> str:
    aliases = {
        "gold": "gold_precious_metals",
        "precious_metals": "gold_precious_metals",
        "oil": "oil_energy",
        "energy": "oil_energy",
    }
    return aliases.get(theme, theme)
