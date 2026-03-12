"""LLM-as-judge for answer relevance scoring."""

from __future__ import annotations

import json
import logging

from src.generation.llm_client import generate_answer

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = """You are an expert evaluator for a RAG (Retrieval-Augmented Generation) system.
You will be given a question, an expected answer, and the system's actual answer.

Score the actual answer on a scale of 1-5:
1 = Completely wrong or irrelevant
2 = Partially relevant but mostly incorrect or missing key information
3 = Somewhat relevant, captures some key points but with significant gaps
4 = Good answer, covers most key points with minor gaps or inaccuracies
5 = Excellent answer, comprehensive and accurate

Respond with ONLY a JSON object: {"score": <1-5>, "reason": "<brief explanation>"}"""


async def judge_answer(
    question: str,
    expected_answer: str,
    actual_answer: str,
) -> dict:
    """Use LLM to score answer relevance.

    Returns:
        {"score": int, "reason": str}
    """
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Expected Answer: {expected_answer}\n\n"
                f"Actual Answer: {actual_answer}"
            ),
        },
    ]
    try:
        raw = await generate_answer(messages)
        # Parse JSON from response
        # Handle cases where LLM wraps in markdown code block
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text)
        score = int(result.get("score", 0))
        return {"score": max(1, min(5, score)), "reason": result.get("reason", "")}
    except Exception as e:
        logger.warning(f"Judge failed: {e}")
        return {"score": 0, "reason": f"Judge error: {e}"}
