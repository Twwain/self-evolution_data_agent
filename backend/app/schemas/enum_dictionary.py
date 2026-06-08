"""EnumDictionary Pydantic schemas — create / update / response."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EnumValueItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    db_value: int | str
    description: str | None = Field(None, max_length=200)


class EnumDictionaryCreate(BaseModel):
    namespace_id: int = Field(..., gt=0)
    enum_class_name: str = Field(..., min_length=1, max_length=100)
    fully_qualified_name: str | None = Field(None, max_length=200)
    values: list[EnumValueItem] = Field(..., min_length=1)
    scope: Literal["namespace", "global"] = "namespace"
    comment: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _types_consistent(self):
        types = {type(v.db_value) for v in self.values}
        if len(types) > 1:
            raise ValueError("values 内 db_value 必须类型一致 (全 int 或全 str)")
        return self


class EnumDictionaryUpdate(BaseModel):
    values: list[EnumValueItem] | None = None
    comment: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _types_consistent(self):
        if self.values:
            types = {type(v.db_value) for v in self.values}
            if len(types) > 1:
                raise ValueError("values 内 db_value 必须类型一致")
        return self


class EnumDictionaryResponse(BaseModel):
    id: int
    namespace_id: int
    enum_class_name: str
    fully_qualified_name: str | None
    values: list[EnumValueItem]
    scope: str
    source: str
    comment: str
    reference_count: int = 0
    created_at: datetime
    updated_at: datetime
