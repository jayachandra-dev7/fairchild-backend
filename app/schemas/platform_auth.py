from pydantic import BaseModel, Field


class PlatformAuthorizeRequest(BaseModel):
    token: str = Field(min_length=1)


class PlatformAuthorizeResponse(BaseModel):
    platform: str
    authorized: bool
    message: str
