from pydantic import BaseModel, Field


DEFAULT_MODEL_CANDIDATES = [
    'claude-sonnet-4-5',
    'claude-opus-4-1',
    'claude-3-7-sonnet-latest',
    'claude-3-5-sonnet-latest',
    'claude-3-5-haiku-latest',
]


class ClaudeGenerateRequest(BaseModel):
    prompt: str = Field(default='Write 3 short title ideas for a product post.', min_length=1)
    model_candidates: list[str] = Field(default_factory=lambda: DEFAULT_MODEL_CANDIDATES.copy(), alias='modelCandidates')
    max_tokens: int = Field(default=500, ge=1, le=4096, alias='maxTokens')
    temperature: float = Field(default=0.7, ge=0, le=1)


class ClaudeGenerateResponse(BaseModel):
    model: str
    text: str
