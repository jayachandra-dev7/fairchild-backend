from typing import Any

from pydantic import BaseModel, Field


class RenderFormRenderRequest(BaseModel):
    template: str = Field(default='noisy-griffins-play-safely-1558', min_length=1)
    title_text: str = Field(default='AUTOMATION TEST', alias='titleText')
    image_src: str = Field(alias='imageSrc', min_length=1)
    extra_data: dict[str, Any] = Field(default_factory=dict, alias='extraData')
