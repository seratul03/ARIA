from __future__ import annotations

import re

from aria.tools.base import BaseTool, TestCase, ToolResult
from aria.config import settings
from groq import Groq

class SummarizerTool(BaseTool):
    name = "summarizer_tool"

    def run(self, input: dict) -> ToolResult:
        text = str(input.get("text", "")).strip()
        max_sentences = int(input.get("max_sentences", 3))
        mode = str(input.get("mode", "llm")).lower()

        if not text:
            return ToolResult(success=False, output=None, error="No text provided.")

        if len(text) < 50:
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
        groq_limiter = Groq(api_key=settings.groq_api_key)
        response = groq_limiter.chat.completions.create(
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
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(sentences) <= max_sentences:
            return text.strip()

        words = re.findall(r'\b[a-z]+\b', text.lower())
        freq: dict[str, int] = {}
        for word in words:
            if len(word) > 3:  # Skip short stop words
                freq[word] = freq.get(word, 0) + 1

        def score(sentence: str) -> float:
            ws = re.findall(r'\b[a-z]+\b', sentence.lower())
            return sum(freq.get(w, 0) for w in ws) / max(len(ws), 1)

        scored = sorted(
            enumerate(sentences),
            key=lambda x: score(x[1]),
            reverse=True,
        )

        top_indices = sorted(idx for idx, _ in scored[:max_sentences])
        return " ".join(sentences[i] for i in top_indices)