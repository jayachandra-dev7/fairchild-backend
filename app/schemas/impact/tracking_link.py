from pydantic import BaseModel


class ImpactTrackingLinkCreateRequest(BaseModel):
    deeplink: str | None = None
    shared_id: str | None = None
    sub_id1: str | None = None
    sub_id2: str | None = None
    sub_id3: str | None = None
    sub_id4: str | None = None
    media_partner_property_id: str | None = None
