import json
import unittest

from portfolio_news_agent.telegram_sender import (
    TelegramConfigError,
    TelegramSendError,
    format_telegram_message,
    send_telegram_message,
)


class TelegramSenderTests(unittest.TestCase):
    def test_formats_compact_plain_text_message(self):
        message = format_telegram_message(
            {
                "symbol": "AEM",
                "inferred_sentiment": "somewhat_bullish",
                "action_relevance": "portfolio_attention",
                "headline": "Agnico Eagle margin outlook improves",
                "author_rating": "Buy",
                "quant_rating": "Hold",
                "wall_street_rating": "Strong Buy",
                "price_targets_json": '["78"]',
                "forward_data_json": '{"margin":"higher"}',
                "short_summary": "Margins improved.\nGold prices remain supportive.",
                "source_url": "https://seekingalpha.com/article/1",
            }
        )

        self.assertIn("AEM - somewhat_bullish - portfolio_attention", message)
        self.assertIn("Author rating: Buy", message)
        self.assertIn("Quant: Hold | Wall St: Strong Buy", message)
        self.assertIn('Forward data: ["78"] | {"margin":"higher"}', message)
        self.assertTrue(message.endswith("https://seekingalpha.com/article/1"))

    def test_missing_token_or_chat_id_raises_clear_config_error(self):
        with self.assertRaises(TelegramConfigError) as context:
            send_telegram_message(
                bot_token="",
                chat_id="12345",
                text="hello",
                transport=lambda url, payload: {"ok": True},
            )

        self.assertIn("TELEGRAM_BOT_TOKEN", str(context.exception))

    def test_send_uses_plain_text_payload_without_parse_mode(self):
        calls = []

        def fake_transport(url, payload):
            calls.append((url, payload))
            return {"ok": True, "result": {"message_id": 42}}

        response = send_telegram_message(
            bot_token="token",
            chat_id="12345",
            text="AEM - neutral - monitor",
            transport=fake_transport,
        )

        self.assertEqual(response["result"]["message_id"], 42)
        self.assertEqual(calls[0][0], "https://api.telegram.org/bottoken/sendMessage")
        self.assertEqual(
            json.loads(calls[0][1].decode("utf-8")),
            {"chat_id": "12345", "text": "AEM - neutral - monitor"},
        )

    def test_send_raises_on_telegram_error_response(self):
        with self.assertRaises(TelegramSendError) as context:
            send_telegram_message(
                bot_token="token",
                chat_id="12345",
                text="hello",
                transport=lambda url, payload: {
                    "ok": False,
                    "description": "chat not found",
                },
            )

        self.assertIn("chat not found", str(context.exception))
