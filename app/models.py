import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


FieldStatus = Literal["PASS", "FAIL"]
VerificationVerdict = Literal["PASS", "NEEDS_REVIEW"]
BatchItemOutcome = Literal["PASS", "NEEDS_REVIEW", "ERROR"]


class ApplicationData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_name: str = Field(max_length=200)
    product_class: str = Field(max_length=200)
    producer: str = Field(max_length=300)
    country: str = Field(max_length=100)
    abv: str = Field(max_length=50)
    net_contents: str = Field(max_length=50)
    government_warning: str = Field(max_length=4000)

    @field_validator("*")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("abv")
    @classmethod
    def abv_must_be_valid(cls, value: str) -> str:
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match is None or not 0 < float(match.group()) <= 100:
            raise ValueError("must contain an alcohol percentage between 0 and 100")
        return value

    @field_validator("net_contents")
    @classmethod
    def net_contents_must_be_valid(cls, value: str) -> str:
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(ml|milliliters?|millilitres?|l|liters?|litres?)\s*",
            value,
            re.IGNORECASE,
        )
        if match is None or float(match.group(1)) <= 0:
            raise ValueError("must include a positive amount in mL or L")
        return value


class ExtractedLabel(BaseModel):
    brand_name: str | None = None
    product_class: str | None = None
    producer: str | None = None
    country: str | None = None
    abv: str | None = None
    net_contents: str | None = None
    government_warning: str | None = None


class FieldResult(BaseModel):
    field_name: str
    expected: str
    actual: str
    status: FieldStatus
    reason: str
    score: float | None = None


class VerificationResult(BaseModel):
    verdict: VerificationVerdict
    fields: list[FieldResult]
    latency_ms: int | None = None


class BatchSummary(BaseModel):
    passed: int
    needs_review: int
    total: int


class BatchItemResult(BaseModel):
    index: int
    filename: str
    outcome: BatchItemOutcome
    result: VerificationResult | None = None
    error: str | None = None


class BatchVerificationResult(BaseModel):
    summary: BatchSummary
    items: list[BatchItemResult]
    latency_ms: int
