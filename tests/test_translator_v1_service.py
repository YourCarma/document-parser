import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from modules.translator.v1.service import CustomModelTranslator
from modules.translator.v1.utils import RetryableUpstreamError


class TranslatorV1ServiceTests(unittest.IsolatedAsyncioTestCase):
    def make_translator(
        self,
        max_concurrency: int = 2,
        shared_semaphore: asyncio.Semaphore | None = None,
    ) -> CustomModelTranslator:
        return CustomModelTranslator(
            source="unused",
            source_language="en",
            target_language="ru",
            include_image_in_output=False,
            max_concurrency=max_concurrency,
            shared_semaphore=shared_semaphore,
        )

    async def test_batches_are_bounded_and_results_keep_input_order(self):
        translator = self.make_translator(max_concurrency=2)
        active = 0
        maximum_active = 0

        async def translate(text: str) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.001 if text == "slow" else 0)
            active -= 1
            return f"translated:{text}"

        translator.translate_element = translate
        result = await translator._translate_in_batches(
            ["slow", "fast", "third", "fourth", "fifth"]
        )

        self.assertEqual(
            result,
            [
                "translated:slow",
                "translated:fast",
                "translated:third",
                "translated:fourth",
                "translated:fifth",
            ],
        )
        self.assertLessEqual(maximum_active, 2)

    async def test_shared_semaphore_limits_multiple_translators(self):
        shared = asyncio.Semaphore(1)
        first = self.make_translator(max_concurrency=3, shared_semaphore=shared)
        second = self.make_translator(max_concurrency=3, shared_semaphore=shared)
        active = 0
        maximum_active = 0

        async def translate(text: str) -> str:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            return text

        first.translate_element = translate
        second.translate_element = translate
        await asyncio.gather(
            first._translate_in_batches(["one", "two"]),
            second._translate_in_batches(["three", "four"]),
        )

        self.assertEqual(maximum_active, 1)

    async def test_translate_element_retries_temporary_upstream_errors(self):
        translator = self.make_translator()
        upstream = AsyncMock(
            side_effect=[
                RetryableUpstreamError(status_code=502, detail="temporary"),
                RetryableUpstreamError(status_code=502, detail="temporary"),
                {"text": "success"},
            ]
        )

        with (
            patch("modules.translator.v1.service.post_request", upstream),
            patch("modules.translator.v1.utils.asyncio.sleep", AsyncMock()),
        ):
            result = await translator.translate_element("source")

        self.assertEqual(result, "success")
        self.assertEqual(upstream.await_count, 3)

    async def test_translate_element_rejects_missing_text(self):
        translator = self.make_translator()
        with patch(
            "modules.translator.v1.service.post_request",
            AsyncMock(return_value={}),
        ):
            with self.assertRaises(ValueError):
                await translator.translate_element("source")

    async def test_translate_element_reuses_configured_http_session(self):
        session = object()
        translator = self.make_translator()
        translator.http_session = session
        upstream = AsyncMock(return_value={"text": "success"})

        with patch("modules.translator.v1.service.post_request", upstream):
            result = await translator.translate_element("source")

        self.assertEqual(result, "success")
        self.assertIs(upstream.await_args.kwargs["session"], session)


if __name__ == "__main__":
    unittest.main()
