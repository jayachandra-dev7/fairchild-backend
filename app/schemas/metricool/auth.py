from pydantic import BaseModel, Field


class MetricoolAuthorizeRequest(BaseModel):
    token: str = Field(min_length=1)


class MetricoolAuthorizeResponse(BaseModel):
    platform: str
    authorized: bool
    message: str
