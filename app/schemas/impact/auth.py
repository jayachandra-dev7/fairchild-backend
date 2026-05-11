from pydantic import BaseModel, Field


class ImpactAuthorizeRequest(BaseModel):
    account_sid: str = Field(min_length=1)
    auth_token: str = Field(min_length=1)


class ImpactAuthorizeResponse(BaseModel):
    platform: str
    account_sid: str
    authorized: bool
    message: str
