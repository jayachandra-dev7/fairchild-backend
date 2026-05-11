class PlatformAuthService:
    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def set_token(self, platform: str, token: str) -> None:
        self._tokens[platform] = token.strip()

    def get_token(self, platform: str) -> str | None:
        token = self._tokens.get(platform)
        if token:
            return token
        return None


platform_auth_service = PlatformAuthService()
