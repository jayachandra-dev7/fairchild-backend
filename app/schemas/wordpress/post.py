from pydantic import BaseModel, Field


class WordPressPostCreateRequest(BaseModel):
    title: str = Field(
        min_length=1,
        description='Post title.',
        examples=['Your AI-generated title'],
    )
    content: str = Field(
        min_length=1,
        description='Post body as HTML.',
        examples=['<p>Your AI-generated HTML body...</p>'],
    )
    status: str = Field(
        default='draft',
        description="Publish state. WordPress accepts 'publish', 'draft', 'pending', 'private', 'future'.",
        examples=['publish'],
    )
    # WordPress core posts API expects a flat array of category IDs (e.g. [1]),
    # unlike WooCommerce products which expect [{ "id": 1 }].
    categories: list[int] = Field(
        default_factory=list,
        description='Category IDs to attach to the post.',
        examples=[[1]],
    )
    tags: list[int] = Field(
        default_factory=list,
        description='Tag IDs to attach to the post.',
    )
    excerpt: str = Field(default='', description='Optional post excerpt/summary.')
    slug: str | None = Field(default=None, description='Optional URL slug; WordPress derives one from the title if omitted.')
    featured_media: int | None = Field(
        default=None,
        description='Optional media ID for the featured image (e.g. the id returned by /wordpress/media/upload).',
    )
