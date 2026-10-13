from pydantic import BaseModel, Field, field_validator


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=1_000_000)

    @field_validator("code")
    @classmethod
    def nonblank(cls, value: str):
        if not value.strip():
            raise ValueError("Code cannot be blank")
        return value
