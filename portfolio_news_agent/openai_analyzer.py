from __future__ import annotations

import json
from typing import Any, Protocol


VALID_SENTIMENTS = {
    "bullish",
    "somewhat_bullish",
    "neutral",
    "mixed",
    "somewhat_bearish",
    "bearish",
    "unclear",
}

VALID_ACTION_RELEVANCES = {
    "ignore",
    "monitor",
    "earnings",
    "valuation",
    "price_target",
    "thesis_change",
    "risk_warning",
    "material_news",
    "portfolio_attention",
}

VALID_THEMES = {"bullish", "bearish", "mixed", "neutral", "unclear"}

SAFE_ASSET_FIELDS = {
    "symbol",
    "name",
    "asset_type",
    "sector",
    "industry",
    "price",
    "quant_rating",
    "sa_analyst_rating",
    "wall_street_rating",
}

MAX_ANALYSIS_BODY_CHARS = 5_000


class AnalysisClient(Protocol):
    def create_structured_response(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Return parsed JSON from a model response."""


class LLMAnalysisError(RuntimeError):
    """Raised when model output cannot be used safely."""


class OpenAIResponsesClient:
    def __init__(self, openai_client: Any | None = None, api_key: str | None = None) -> None:
        if openai_client is None:
            from openai import OpenAI

            kwargs = {}
            if api_key:
                kwargs["api_key"] = api_key
            openai_client = OpenAI(**kwargs)
        self._client = openai_client

    def create_structured_response(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = self._client.responses.create(
                model=model,
                input=messages,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "portfolio_news_analysis",
                        "strict": True,
                        "schema": schema,
                    }
                },
            )
        except Exception as exc:
            raise LLMAnalysisError(f"OpenAI request failed: {_safe_exception_message(exc)}") from exc
        return _extract_json_response(response)


def analyze_article(
    *,
    client: AnalysisClient,
    model: str,
    article: dict[str, Any],
    portfolio_assets: list[dict[str, Any]],
    commodity_exposures: dict[str, Any],
    prompt_version: str,
    max_retries: int = 1,
) -> dict[str, Any]:
    messages = build_analysis_messages(
        article=article,
        portfolio_assets=portfolio_assets,
        commodity_exposures=commodity_exposures,
    )
    schema = analysis_schema()
    allowed_symbols = {
        str(asset.get("symbol", "")).strip().upper()
        for asset in portfolio_assets
        if str(asset.get("symbol", "")).strip()
    }
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = client.create_structured_response(
                model=model,
                messages=_messages_for_attempt(messages, attempt),
                schema=schema,
            )
            result = parse_and_validate_analysis(response, allowed_symbols=allowed_symbols)
            result["prompt_version"] = prompt_version
            result["llm_model"] = model
            return result
        except LLMAnalysisError as exc:
            last_error = exc

    raise LLMAnalysisError(f"failed_llm: {last_error}") from last_error


def build_analysis_messages(
    *,
    article: dict[str, Any],
    portfolio_assets: list[dict[str, Any]],
    commodity_exposures: dict[str, Any],
) -> list[dict[str, str]]:
    safe_assets = [_safe_asset(asset) for asset in portfolio_assets]
    body_text = str(article.get("body_text") or "")
    trimmed_body = _trim_article_body(body_text)
    payload = {
        "article": {
            "headline": article.get("headline"),
            "author": article.get("author"),
            "article_date": article.get("article_date"),
            "source_url": article.get("source_url"),
            "body_text": trimmed_body,
            "body_characters_original": len(body_text),
            "body_truncated": len(trimmed_body) < len(body_text),
        },
        "portfolio_assets": safe_assets,
        "commodity_exposures": commodity_exposures,
    }
    return [
        {
            "role": "system",
            "content": (
                "You classify Seeking Alpha articles against a user's portfolio. "
                "Return only the requested JSON schema. Do not provide trading advice. "
                "Use only portfolio symbols supplied in the payload."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=True, sort_keys=True),
        },
    ]


def analysis_schema() -> dict[str, Any]:
    asset_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string"},
            "company_name": {"type": ["string", "null"]},
            "theme": {"type": "string", "enum": sorted(VALID_THEMES)},
            "author_rating": {"type": ["string", "null"]},
            "quant_rating": {"type": ["string", "null"]},
            "wall_street_rating": {"type": ["string", "null"]},
            "inferred_sentiment": {"type": "string", "enum": sorted(VALID_SENTIMENTS)},
            "price_targets": {"type": "array", "items": {"type": "string"}},
            "forward_data": {"type": "array", "items": {"type": "string"}},
            "action_relevance": {
                "type": "string",
                "enum": sorted(VALID_ACTION_RELEVANCES),
            },
            "short_summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "symbol",
            "company_name",
            "theme",
            "author_rating",
            "quant_rating",
            "wall_street_rating",
            "inferred_sentiment",
            "price_targets",
            "forward_data",
            "action_relevance",
            "short_summary",
            "confidence",
        ],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "relevant_assets": {"type": "array", "items": asset_schema},
            "irrelevant_reason": {"type": ["string", "null"]},
        },
        "required": ["relevant_assets", "irrelevant_reason"],
    }


def parse_and_validate_analysis(
    response: Any,
    *,
    allowed_symbols: set[str],
) -> dict[str, Any]:
    data = _coerce_response_payload(response)
    _raise_for_refusal_or_incomplete(data)
    if not isinstance(data, dict):
        raise LLMAnalysisError("Model output must be a JSON object")

    relevant_assets = data.get("relevant_assets")
    if not isinstance(relevant_assets, list):
        raise LLMAnalysisError("relevant_assets must be a list")

    filtered_assets = []
    for index, asset in enumerate(relevant_assets):
        normalized = _validate_asset_summary(asset, allowed_symbols, index)
        if normalized is not None:
            filtered_assets.append(normalized)

    irrelevant_reason = data.get("irrelevant_reason")
    if irrelevant_reason is not None and not isinstance(irrelevant_reason, str):
        raise LLMAnalysisError("irrelevant_reason must be a string or null")

    return {
        "relevant_assets": filtered_assets,
        "irrelevant_reason": irrelevant_reason,
    }


def _validate_asset_summary(
    asset: Any,
    allowed_symbols: set[str],
    index: int,
) -> dict[str, Any] | None:
    if not isinstance(asset, dict):
        raise LLMAnalysisError(f"relevant_assets[{index}] must be an object")

    symbol = str(asset.get("symbol", "")).strip().upper()
    if not symbol:
        raise LLMAnalysisError(f"relevant_assets[{index}].symbol is required")
    if symbol not in allowed_symbols:
        return None

    inferred_sentiment = _required_enum(
        asset,
        "inferred_sentiment",
        VALID_SENTIMENTS,
        index,
    )
    action_relevance = _required_enum(
        asset,
        "action_relevance",
        VALID_ACTION_RELEVANCES,
        index,
    )
    theme = _required_enum(asset, "theme", VALID_THEMES, index)
    short_summary = _required_string(asset, "short_summary", index)
    confidence = _required_confidence(asset, index)

    return {
        "symbol": symbol,
        "company_name": _optional_string(asset.get("company_name")),
        "theme": theme,
        "author_rating": _optional_string(asset.get("author_rating")),
        "quant_rating": _optional_string(asset.get("quant_rating")),
        "wall_street_rating": _optional_string(asset.get("wall_street_rating")),
        "inferred_sentiment": inferred_sentiment,
        "price_targets": _optional_string_list(asset.get("price_targets")),
        "forward_data": _optional_string_list(asset.get("forward_data")),
        "action_relevance": action_relevance,
        "short_summary": short_summary,
        "confidence": confidence,
    }


def _messages_for_attempt(
    messages: list[dict[str, str]],
    attempt: int,
) -> list[dict[str, str]]:
    if attempt == 0:
        return messages
    retry_instruction = {
        "role": "user",
        "content": (
            "Retry using exactly the required JSON schema. Do not include symbols "
            "outside the provided portfolio. Do not infer missing facts."
        ),
    }
    return [*messages, retry_instruction]


def _extract_json_response(response: Any) -> dict[str, Any]:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return _loads_json(output_text)
    if isinstance(response, dict):
        return response
    raise LLMAnalysisError("OpenAI response did not include output_text")


def _coerce_response_payload(response: Any) -> Any:
    if isinstance(response, str):
        return _loads_json(response)
    return response


def _loads_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise LLMAnalysisError(f"Malformed JSON: {exc.msg}") from exc


def _safe_exception_message(exc: Exception) -> str:
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    return text


def _trim_article_body(body_text: str) -> str:
    if len(body_text) <= MAX_ANALYSIS_BODY_CHARS:
        return body_text
    return body_text[:MAX_ANALYSIS_BODY_CHARS].rstrip()


def _raise_for_refusal_or_incomplete(data: Any) -> None:
    if not isinstance(data, dict):
        return
    if data.get("refusal"):
        raise LLMAnalysisError("Model refusal")
    if data.get("status") == "incomplete" or data.get("incomplete_details"):
        raise LLMAnalysisError("Incomplete model output")


def _safe_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {
        key: asset.get(key)
        for key in sorted(SAFE_ASSET_FIELDS)
        if asset.get(key) not in (None, "")
    }


def _required_enum(
    asset: dict[str, Any],
    field: str,
    allowed_values: set[str],
    index: int,
) -> str:
    value = asset.get(field)
    if not isinstance(value, str) or value not in allowed_values:
        raise LLMAnalysisError(
            f"relevant_assets[{index}].{field} must be one of {sorted(allowed_values)}"
        )
    return value


def _required_string(asset: dict[str, Any], field: str, index: int) -> str:
    value = asset.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LLMAnalysisError(f"relevant_assets[{index}].{field} is required")
    return value.strip()


def _required_confidence(asset: dict[str, Any], index: int) -> float:
    value = asset.get("confidence")
    if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
        raise LLMAnalysisError(
            f"relevant_assets[{index}].confidence must be a number from 0 to 1"
        )
    return float(value)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise LLMAnalysisError("Expected a list of strings")
    return [str(item).strip() for item in value if str(item).strip()]
