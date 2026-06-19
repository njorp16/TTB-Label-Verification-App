from typing import Literal

from pydantic import BaseModel


FieldStatus = Literal["PASS", "FAIL"]
VerificationVerdict = Literal["PASS", "NEEDS_REVIEW"]


class ApplicationData(BaseModel):
    brand_name: str
    product_class: str
    producer: str
    country: str
    abv: str
    net_contents: str
    government_warning: str


class ExtractedLabel(BaseModel):
    brand_name: str
    product_class: str
    producer: str
    country: str
    abv: str
    net_contents: str
    government_warning: str


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
