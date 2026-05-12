from typing import Any

from pydantic import BaseModel, Field


class MetricoolProvider(BaseModel):
    network: str = Field(min_length=1)


class MetricoolPublicationDate(BaseModel):
    date_time: str = Field(alias='dateTime', min_length=1)
    timezone: str = Field(min_length=1)


class MetricoolSmartLinkData(BaseModel):
    ids: list[str] = Field(default_factory=list)


class MetricoolTwitterData(BaseModel):
    tags: list[str] = Field(default_factory=list)
    type: str = Field(default='POST')
    reply_settings: str | None = Field(default=None, alias='replySettings')


class MetricoolSchedulerPostRequest(BaseModel):
    shortener: bool = False
    draft: bool = True
    text: str = Field(min_length=1)
    first_comment_text: str = Field(default='', alias='firstCommentText')
    auto_publish: bool = Field(default=True, alias='autoPublish')
    media: list[str] = Field(default_factory=list)
    providers: list[MetricoolProvider] = Field(default_factory=list)
    publication_date: MetricoolPublicationDate = Field(alias='publicationDate')
    media_alt_text: list[str | None] = Field(default_factory=list, alias='mediaAltText')
    has_not_read_notes: bool = Field(default=False, alias='hasNotReadNotes')
    performance_dashboard_ids: list[str] = Field(default_factory=list, alias='performanceDashboardIds')
    descendants: list[Any] = Field(default_factory=list)
    smart_link_data: MetricoolSmartLinkData = Field(default_factory=MetricoolSmartLinkData, alias='smartLinkData')
    twitter_data: MetricoolTwitterData = Field(default_factory=MetricoolTwitterData, alias='twitterData')
