import json
import unittest

from portfolio_news_agent.openai_analyzer import (
    LLMAnalysisError,
    MAX_ANALYSIS_BODY_CHARS,
    OpenAIResponsesClient,
    analyze_article,
    build_analysis_messages,
    parse_and_validate_analysis,
)


class FakeAnalysisClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def create_structured_response(self, *, model, messages, schema):
        self.calls.append({"model": model, "messages": messages, "schema": schema})
        if not self.responses:
            raise AssertionError("No fake response left")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class OpenAIAnalyzerTests(unittest.TestCase):
    def test_schema_validation_rejects_unknown_sentiment(self):
        with self.assertRaises(LLMAnalysisError) as context:
            parse_and_validate_analysis(
                {
                    "relevant_assets": [
                        {
                            "symbol": "AEM",
                            "company_name": "Agnico Eagle Mines",
                            "theme": "bullish",
                            "author_rating": "Buy",
                            "quant_rating": "Hold",
                            "wall_street_rating": "Strong Buy",
                            "inferred_sentiment": "very excited",
                            "price_targets": [],
                            "forward_data": [],
                            "action_relevance": "portfolio_attention",
                            "short_summary": "Margins improved.",
                            "confidence": 0.9,
                        }
                    ],
                    "irrelevant_reason": None,
                },
                allowed_symbols={"AEM"},
            )

        self.assertIn("inferred_sentiment", str(context.exception))

    def test_prompt_construction_does_not_include_secrets(self):
        messages = build_analysis_messages(
            article={
                "headline": "AEM margin outlook improves",
                "source_url": "https://seekingalpha.com/article/1",
                "body_text": "Gold margins improved.",
            },
            portfolio_assets=[
                {
                    "symbol": "AEM",
                    "name": "Agnico Eagle Mines",
                    "quant_rating": "Hold",
                    "wall_street_rating": "Strong Buy",
                    "openai_api_key": "secret-openai-key",
                    "telegram_bot_token": "secret-telegram-token",
                }
            ],
            commodity_exposures={"gold_precious_metals": [{"symbol": "AEM"}]},
        )

        serialized = json.dumps(messages)
        self.assertIn("AEM", serialized)
        self.assertIn("Gold margins improved.", serialized)
        self.assertNotIn("secret-openai-key", serialized)
        self.assertNotIn("secret-telegram-token", serialized)

    def test_prompt_trims_long_article_body_to_reduce_token_use(self):
        long_body = "A" * (MAX_ANALYSIS_BODY_CHARS + 500)

        messages = build_analysis_messages(
            article={
                "headline": "Long article",
                "source_url": "https://seekingalpha.com/article/long",
                "body_text": long_body,
            },
            portfolio_assets=[{"symbol": "AEM", "name": "Agnico Eagle Mines"}],
            commodity_exposures={},
        )

        payload = json.loads(messages[1]["content"])

        self.assertEqual(
            payload["article"]["body_text"],
            "A" * MAX_ANALYSIS_BODY_CHARS,
        )
        self.assertEqual(payload["article"]["body_characters_original"], len(long_body))
        self.assertTrue(payload["article"]["body_truncated"])

    def test_v2_prompt_contract_uses_deeper_body_and_restricts_macro_fanout(self):
        body = "ASML direct order backlog improved. " * 1200

        messages = build_analysis_messages(
            article={
                "headline": "Semiconductor cycle looks better",
                "source_url": "https://seekingalpha.com/article/semis",
                "body_text": body,
            },
            portfolio_assets=[
                {"symbol": "ASML", "name": "ASML Holding"},
                {"symbol": "NVDA", "name": "Nvidia"},
            ],
            commodity_exposures={},
        )

        payload = json.loads(messages[1]["content"])
        system_prompt = messages[0]["content"]

        self.assertEqual(MAX_ANALYSIS_BODY_CHARS, 30_000)
        self.assertLessEqual(len(payload["article"]["body_text"]), 30_000)
        self.assertIn("Goal:", system_prompt)
        self.assertIn("company-specific evidence", system_prompt)
        self.assertIn("Do not list every related portfolio stock", system_prompt)
        self.assertIn("Return JSON only", system_prompt)
        self.assertIn("analysis_goal", payload)
        self.assertIn("ticker_relevance_rules", payload)
        self.assertIn("evidence_requirements", payload)
        self.assertIn("output_contract", payload)
        self.assertIn("direct company-specific evidence", " ".join(payload["ticker_relevance_rules"]))
        self.assertIn("macro", " ".join(payload["ticker_relevance_rules"]).lower())

    def test_mocked_single_stock_response_returns_relevant_asset(self):
        client = FakeAnalysisClient(
            {
                "relevant_assets": [
                    {
                        "symbol": "AEM",
                        "company_name": "Agnico Eagle Mines",
                        "theme": "bullish",
                        "author_rating": "Buy",
                        "quant_rating": "Hold",
                        "wall_street_rating": "Strong Buy",
                        "inferred_sentiment": "somewhat_bullish",
                        "price_targets": ["78"],
                        "forward_data": ["Higher gold margins"],
                        "action_relevance": "portfolio_attention",
                        "short_summary": "Margins improved.\nGold prices support cash flow.",
                        "confidence": 0.87,
                    }
                ],
                "irrelevant_reason": None,
            }
        )

        result = analyze_article(
            client=client,
            model="gpt-5-nano",
            article={"headline": "AEM update", "body_text": "AEM margins improved."},
            portfolio_assets=[{"symbol": "AEM", "name": "Agnico Eagle Mines"}],
            commodity_exposures={},
            prompt_version="v1",
        )

        self.assertEqual(len(result["relevant_assets"]), 1)
        self.assertEqual(result["relevant_assets"][0]["symbol"], "AEM")
        self.assertEqual(result["relevant_assets"][0]["action_relevance"], "portfolio_attention")
        self.assertEqual(client.calls[0]["model"], "gpt-5-nano")
        self.assertEqual(result["prompt_version"], "v1")

    def test_mocked_multi_stock_response_filters_non_portfolio_symbol(self):
        client = FakeAnalysisClient(
            {
                "relevant_assets": [
                    {
                        "symbol": "AEM",
                        "company_name": "Agnico Eagle Mines",
                        "theme": "bullish",
                        "inferred_sentiment": "bullish",
                        "action_relevance": "material_news",
                        "short_summary": "AEM was discussed directly.",
                        "confidence": 0.8,
                    },
                    {
                        "symbol": "TSLA",
                        "company_name": "Tesla",
                        "theme": "unclear",
                        "inferred_sentiment": "unclear",
                        "action_relevance": "ignore",
                        "short_summary": "Not in portfolio.",
                        "confidence": 0.2,
                    },
                ],
                "irrelevant_reason": None,
            }
        )

        result = analyze_article(
            client=client,
            model="gpt-5-nano",
            article={"headline": "Mixed market notes", "body_text": "AEM and TSLA mentioned."},
            portfolio_assets=[{"symbol": "AEM", "name": "Agnico Eagle Mines"}],
            commodity_exposures={},
            prompt_version="v1",
        )

        self.assertEqual([asset["symbol"] for asset in result["relevant_assets"]], ["AEM"])

    def test_broad_article_fanout_keeps_only_tickers_with_direct_article_evidence(self):
        client = FakeAnalysisClient(
            {
                "relevant_assets": [
                    {
                        "symbol": "ASML",
                        "company_name": "ASML Holding",
                        "theme": "bullish",
                        "inferred_sentiment": "somewhat_bullish",
                        "action_relevance": "portfolio_attention",
                        "short_summary": "ASML backlog is directly discussed.",
                        "confidence": 0.82,
                    },
                    {
                        "symbol": "NVDA",
                        "company_name": "Nvidia",
                        "theme": "bullish",
                        "inferred_sentiment": "somewhat_bullish",
                        "action_relevance": "portfolio_attention",
                        "short_summary": "Generic semiconductor exposure.",
                        "confidence": 0.62,
                    },
                    {
                        "symbol": "AMZN",
                        "company_name": "Amazon",
                        "theme": "bullish",
                        "inferred_sentiment": "somewhat_bullish",
                        "action_relevance": "portfolio_attention",
                        "short_summary": "Generic consumer exposure.",
                        "confidence": 0.61,
                    },
                ],
                "irrelevant_reason": None,
            }
        )

        result = analyze_article(
            client=client,
            model="gpt-5-nano",
            article={
                "headline": "Semiconductor cycle improves",
                "body_text": "ASML order backlog improved, while the broader sector outlook is better.",
            },
            portfolio_assets=[
                {"symbol": "ASML", "name": "ASML Holding"},
                {"symbol": "NVDA", "name": "Nvidia"},
                {"symbol": "AMZN", "name": "Amazon.com"},
            ],
            commodity_exposures={},
            prompt_version="v2",
        )

        self.assertEqual([asset["symbol"] for asset in result["relevant_assets"]], ["ASML"])

    def test_mocked_irrelevant_response_returns_no_assets(self):
        client = FakeAnalysisClient(
            {
                "relevant_assets": [],
                "irrelevant_reason": "Article is about an unrelated software company.",
            }
        )

        result = analyze_article(
            client=client,
            model="gpt-5-nano",
            article={"headline": "Software update", "body_text": "No portfolio exposure."},
            portfolio_assets=[{"symbol": "AEM", "name": "Agnico Eagle Mines"}],
            commodity_exposures={},
            prompt_version="v1",
        )

        self.assertEqual(result["relevant_assets"], [])
        self.assertIn("unrelated", result["irrelevant_reason"])

    def test_refusal_or_incomplete_output_retries_once_then_fails_llm(self):
        client = FakeAnalysisClient(
            {"refusal": "I cannot help with that."},
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        )

        with self.assertRaises(LLMAnalysisError) as context:
            analyze_article(
                client=client,
                model="gpt-5-nano",
                article={"headline": "AEM update", "body_text": "AEM margins improved."},
                portfolio_assets=[{"symbol": "AEM", "name": "Agnico Eagle Mines"}],
                commodity_exposures={},
                prompt_version="v1",
            )

        self.assertIn("failed_llm", str(context.exception))
        self.assertEqual(len(client.calls), 2)

    def test_openai_wrapper_uses_responses_structured_output_shape(self):
        calls = []

        class FakeResponses:
            def create(self, **kwargs):
                calls.append(kwargs)
                return type("Response", (), {"output_text": '{"relevant_assets":[]}'})()

        class FakeOpenAI:
            def __init__(self):
                self.responses = FakeResponses()

        client = OpenAIResponsesClient(openai_client=FakeOpenAI())
        response = client.create_structured_response(
            model="gpt-5-nano",
            messages=[{"role": "user", "content": "Return JSON"}],
            schema={"type": "object", "properties": {}, "required": []},
        )

        self.assertEqual(response, {"relevant_assets": []})
        self.assertEqual(calls[0]["model"], "gpt-5-nano")
        self.assertEqual(calls[0]["input"][0]["role"], "user")
        self.assertEqual(calls[0]["text"]["format"]["type"], "json_schema")
        self.assertTrue(calls[0]["text"]["format"]["strict"])

    def test_openai_wrapper_converts_api_errors_to_llm_analysis_error(self):
        class FakeResponses:
            def create(self, **kwargs):
                raise RuntimeError("401 invalid_issuer")

        class FakeOpenAI:
            def __init__(self):
                self.responses = FakeResponses()

        client = OpenAIResponsesClient(openai_client=FakeOpenAI())

        with self.assertRaises(LLMAnalysisError) as context:
            client.create_structured_response(
                model="gpt-5-nano",
                messages=[{"role": "user", "content": "Return JSON"}],
                schema={"type": "object", "properties": {}, "required": []},
            )

        self.assertIn("OpenAI request failed", str(context.exception))
