"""Building blocks shared by the request models of several modules."""

from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_core import PydanticCustomError

# ₹1,00,000. Mostly a guard: price columns are 32-bit integers, and a bigger number
# would fail inside the database as a 500 instead of a clear 422 here.
MAX_PRICE_PAISE = 10_000_000

# Prices are whole paise (₹499 = 49900), never floats, which can't hold money exactly.
# 0 means free.
PricePaise = Annotated[int, Field(ge=0, le=MAX_PRICE_PAISE)]


def _reject_null(value: object) -> object:
    if value is None:
        raise PydanticCustomError("not_nullable", "This field can't be empty.")
    return value


# For PATCH models: the field may be left out (no change) but not sent as null.
# Validators don't run for fields that weren't sent, so None here is an explicit null.
# Use as: `name: Annotated[Name | None, NotNull] = None`.
NotNull = BeforeValidator(_reject_null)
