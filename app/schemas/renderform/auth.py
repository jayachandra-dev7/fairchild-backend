from pydantic import BaseModel, Field


class RenderFormAuthorizeRequest(BaseModel):
    api_key: str = Field(min_length=1)


class RenderFormAuthorizeResponse(BaseModel):
    platform: str
    authorized: bool
    message: str
