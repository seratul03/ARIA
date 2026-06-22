"""
aria/tools/summarizer_tool.py
──────────────────────────────
Text summarization tool. Attempts LLM-based summarization via Groq first,
then falls back to extractive summarization (sentence scoring) if unavailable.

LLM calls go through the shared groq_limiter to prevent rate limit errors.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import re

from aria.tools.base import BaseTool, TestCase, ToolResult


class SummarizerTool(BaseTool):
    """
    Summarizes a given text into a shorter form.

    Input:
        text (str): The text to summarize.
        max_sentences (int, optional): Approx. number of sentences in output. Default: 3.
        mode (str, optional): "llm" (default, uses Groq) or "extractive" (no LLM).

    Output:
        A dict with 'summary', 'original_length', and 'summary_length' keys.
    """

    name = "summarizer_tool"

    def run(self, input: dict) -> ToolResult:
        text = str(input.get("text", "")).strip()
        max_sentences = int(input.get("max_sentences", 3))
        mode = str(input.get("mode", "llm")).lower()

        if not text:
            return ToolResult(success=False, output=None, error="No text provided.")

        if len(text) < 50:
            # Text is already short — return as-is
            return ToolResult(
                success=True,
                output={
                    "summary": text,
                    "original_length": len(text),
                    "summary_length": len(text),
                    "method": "passthrough",
                },
            )

        try:
            if mode == "llm":
                summary = self._llm_summarize(text, max_sentences)
                method = "llm"
            else:
                summary = self._extractive_summarize(text, max_sentences)
                method = "extractive"

            return ToolResult(
                success=True,
                output={
                    "summary": summary,
                    "original_length": len(text),
                    "summary_length": len(summary),
                    "method": method,
                },
            )

        except Exception as exc:
            # LLM failed — fall back to extractive
            try:
                summary = self._extractive_summarize(text, max_sentences)
                return ToolResult(
                    success=True,
                    output={
                        "summary": summary,
                        "original_length": len(text),
                        "summary_length": len(summary),
                        "method": "extractive_fallback",
                        "fallback_reason": str(exc),
                    },
                )
            except Exception as fallback_exc:
                return ToolResult(
                    success=False,
                    output=None,
                    error=f"Both LLM and extractive summarization failed: {fallback_exc}",
                )

    def _llm_summarize(self, text: str, max_sentences: int) -> str:
        """Use Groq LLM to generate a concise summary."""
        # Import here to avoid circular imports at module load time
        from aria.core.rate_limiter import groq_limiter
        from aria.config import settings
        from groq import Groq

        groq_limiter.acquire()

        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a concise summarizer. Summarize the following text in "
                        f"approximately {max_sentences} sentences. Return only the summary, "
                        f"no preamble, no labels."
                    ),
                },
                {"role": "user", "content": text[:4000]},  # Groq context limit safety
            ],
            max_tokens=300,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()

    def _extractive_summarize(self, text: str, max_sentences: int) -> str:
        """
        Extractive fallback: score sentences by word frequency and pick top N.
        No LLM required.
        """
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(sentences) <= max_sentences:
            return text.strip()

        # Word frequency scoring
        words = re.findall(r'\b[a-z]+\b', text.lower())
        freq: dict[str, int] = {}
        for word in words:
            if len(word) > 3:  # Skip short stop words
                freq[word] = freq.get(word, 0) + 1

        # Score each sentence
        def score(sentence: str) -> float:
            ws = re.findall(r'\b[a-z]+\b', sentence.lower())
            return sum(freq.get(w, 0) for w in ws) / max(len(ws), 1)

        scored = sorted(
            enumerate(sentences),
            key=lambda x: score(x[1]),
            reverse=True,
        )

        # Pick top N, restore original order
        top_indices = sorted(idx for idx, _ in scored[:max_sentences])
        return " ".join(sentences[i] for i in top_indices)

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="empty_text",
                input={"text": ""},
                expected_success=False
            ),
            TestCase(
                name="short_passthrough",
                input={"text": "This is a very short text snippet."},
                expected_success=True
            ),
            TestCase(
                name="medium_standard_llm",
                input={
                    "text": "Artificial intelligence is a branch of computer science. It involves creating systems capable of performing tasks that typically require human intelligence. These tasks include learning, reasoning, problem-solving, perception, and language understanding. AI has the potential to revolutionize industries.",
                    "mode": "llm",
                    "max_sentences": 2
                },
                expected_success=True
            ),
            TestCase(
                name="extractive_fallback",
                input={
                    "text": "The rapid advancement of technology has dramatically reshaped the modern world. In just a few decades, we have moved from bulky desktop computers to powerful smartphones that fit in our pockets. The internet connects billions of people globally, facilitating instant communication. However, these advancements also bring new ethical dilemmas and risks. Privacy concerns, digital divides, and the potential displacement of jobs by automation are issues that society must carefully navigate.",
                    "mode": "extractive",
                    "max_sentences": 2
                },
                expected_success=True
            )
        ]
