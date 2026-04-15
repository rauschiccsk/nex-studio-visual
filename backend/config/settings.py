from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+pg8000://nexstudio:nexstudio@localhost:9178/nexstudio"
    test_database_url: str = "postgresql+pg8000://nexstudio:nexstudio@localhost:9178/nexstudio_test"
    secret_key: str = "change-me-in-production"
    access_token_expire_minutes: int = 480
    backend_port: int = 9176
    frontend_port: int = 9177
    vite_api_base_url: str = "http://localhost:9176"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
