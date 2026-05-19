from typing import Any

from pydantic import ConfigDict
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


class MetricoolFacebookData(BaseModel):
    type: str = Field(default='POST')


class MetricoolInstagramData(BaseModel):
    type: str = Field(default='POST')
    show_reel_on_feed: bool = Field(default=True, alias='showReelOnFeed')
    collaborators: list[str] = Field(default_factory=list)
    share_trial_automatically: bool = Field(default=False, alias='shareTrialAutomatically')


class MetricoolLinkedInData(BaseModel):
    type: str = Field(default='POST')
    preview_included: bool = Field(default=True, alias='previewIncluded')
    publish_images_as_pdf: bool = Field(default=False, alias='publishImagesAsPDF')


class MetricoolGmbData(BaseModel):
    type: str = Field(default='publication')


class MetricoolTikTokData(BaseModel):
    disable_comment: bool = Field(default=False, alias='disableComment')
    disable_duet: bool = Field(default=False, alias='disableDuet')
    disable_stitch: bool = Field(default=False, alias='disableStitch')
    auto_add_music: bool = Field(default=False, alias='autoAddMusic')
    photo_cover_index: int = Field(default=0, alias='photoCoverIndex')
    is_aigc: bool = Field(default=False, alias='isAigc')
    privacy_option: str = Field(default='public_to_everyone', alias='privacyOption')
    commercial_content_own_brand: bool = Field(default=False, alias='commercialContentOwnBrand')
    commercial_content_third_party: bool = Field(default=False, alias='commercialContentThirdParty')


class MetricoolPinterestData(BaseModel):
    board_id: str = Field(default='', alias='boardId')
    pin_title: str = Field(default='', alias='pinTitle')
    pin_link: str = Field(default='', alias='pinLink')
    pin_new_format: bool = Field(default=False, alias='pinNewFormat')


class MetricoolThreadsData(BaseModel):
    allowed_country_codes: list[str] = Field(default_factory=list, alias='allowedCountryCodes')
    reply_control: str = Field(default='EVERYONE', alias='replyControl')
    type: str = Field(default='POST')
    is_spoiler: bool = Field(default=False, alias='isSpoiler')


class MetricoolSchedulerPostRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            'example': {
                'text': 'Automation Test',
                'autoPublish': True,
                'draft': False,
                'publicationDate': {'dateTime': '2026-05-14T06:53:01', 'timezone': 'America/Denver'},
                'media': ['https://static.metricool.com/common/202605/3410405-mtr_17267611236451227975.jpeg'],
                'shortener': False,
                'firstCommentText': '',
                'providers': [
                    {'network': 'twitter'},
                    {'network': 'facebook'},
                    {'network': 'instagram'},
                    {'network': 'threads'},
                    {'network': 'linkedin'},
                    {'network': 'gmb'},
                    {'network': 'tiktok'},
                ],
                'mediaAltText': [None],
                'hasNotReadNotes': False,
                'performanceDashboardIds': [],
                'descendants': [],
                'smartLinkData': {'ids': []},
                'twitterData': {'tags': [], 'type': 'POST', 'replySettings': None},
                'facebookData': {'type': 'POST'},
                'instagramData': {'type': 'POST', 'showReelOnFeed': True, 'collaborators': [], 'shareTrialAutomatically': False},
                'linkedinData': {'type': 'POST', 'previewIncluded': True, 'publishImagesAsPDF': False},
                'gmbData': {'type': 'publication'},
                'pinterestData': {'boardId': '', 'pinTitle': '', 'pinLink': '', 'pinNewFormat': False},
                'tiktokData': {
                    'disableComment': False,
                    'disableDuet': False,
                    'disableStitch': False,
                    'autoAddMusic': False,
                    'photoCoverIndex': 0,
                    'isAigc': False,
                    'privacyOption': 'public_to_everyone',
                    'commercialContentOwnBrand': False,
                    'commercialContentThirdParty': False,
                },
                'threadsData': {'allowedCountryCodes': [], 'replyControl': 'EVERYONE', 'type': 'POST', 'isSpoiler': False},
            }
        }
    )
    text: str = Field(default='Automation Test', min_length=1)
    auto_publish: bool = Field(default=True, alias='autoPublish')
    draft: bool = False
    publication_date: MetricoolPublicationDate = Field(
        default_factory=lambda: MetricoolPublicationDate(dateTime='2026-05-14T06:53:01', timezone='America/Denver'),
        alias='publicationDate',
    )
    media: list[str] = Field(default_factory=lambda: ['https://static.metricool.com/common/202605/3410405-mtr_17267611236451227975.jpeg'])
    shortener: bool = False
    first_comment_text: str = Field(default='', alias='firstCommentText')
    providers: list[MetricoolProvider] = Field(
        default_factory=lambda: [
            MetricoolProvider(network='twitter'),
            MetricoolProvider(network='facebook'),
            MetricoolProvider(network='instagram'),
            MetricoolProvider(network='threads'),
            MetricoolProvider(network='linkedin'),
            MetricoolProvider(network='gmb'),
            MetricoolProvider(network='tiktok'),
        ]
    )
    media_alt_text: list[str | None] = Field(default_factory=lambda: [None], alias='mediaAltText')
    has_not_read_notes: bool = Field(default=False, alias='hasNotReadNotes')
    performance_dashboard_ids: list[str] = Field(default_factory=list, alias='performanceDashboardIds')
    descendants: list[Any] = Field(default_factory=list)
    smart_link_data: MetricoolSmartLinkData = Field(default_factory=MetricoolSmartLinkData, alias='smartLinkData')
    twitter_data: MetricoolTwitterData = Field(default_factory=MetricoolTwitterData, alias='twitterData')
    facebook_data: MetricoolFacebookData = Field(default_factory=MetricoolFacebookData, alias='facebookData')
    instagram_data: MetricoolInstagramData = Field(default_factory=MetricoolInstagramData, alias='instagramData')
    linkedin_data: MetricoolLinkedInData = Field(default_factory=MetricoolLinkedInData, alias='linkedinData')
    gmb_data: MetricoolGmbData = Field(default_factory=MetricoolGmbData, alias='gmbData')
    pinterest_data: MetricoolPinterestData = Field(default_factory=MetricoolPinterestData, alias='pinterestData')
    tiktok_data: MetricoolTikTokData = Field(default_factory=MetricoolTikTokData, alias='tiktokData')
    threads_data: MetricoolThreadsData = Field(default_factory=MetricoolThreadsData, alias='threadsData')
