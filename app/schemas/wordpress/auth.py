from pydantic import BaseModel, Field


class WordPressAuthorizeRequest(BaseModel):
    domain: str = Field(min_length=1)
    wc_consumer_key: str = Field(min_length=1)
    wc_consumer_secret: str = Field(min_length=1)


class WordPressAuthorizeResponse(BaseModel):
    platform: str
    domain: str
    authorized: bool
    message: str
