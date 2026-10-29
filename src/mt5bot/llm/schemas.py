"""Structured outputs the research adapter may return. Anything else is discarded."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewsCluster(_S):
    title: str = Field(max_length=200)
    item_ids: list[int]
    currencies: list[str]
    summary: str = Field(max_length=1200)


class NewsDigest(_S):
    clusters: list[NewsCluster]
    caveats: str = Field(max_length=600)


class DecisionExplanation(_S):
    explanation: str = Field(max_length=2000)
    key_factors: list[str] = Field(max_length=8)
    caveats: str = Field(max_length=600)


class ResearchHypotheses(_S):
    hypotheses: list[str] = Field(max_length=10)
    suggested_tests: list[str] = Field(max_length=10)
    caveats: str = Field(max_length=600)


class ReportSummary(_S):
    summary: str = Field(max_length=2500)
    concerns: list[str] = Field(max_length=10)
