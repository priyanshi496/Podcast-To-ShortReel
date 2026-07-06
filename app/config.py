import os
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/podcast"
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cpu"
    NVIDIA_API_KEY: str = "your_nvidia_api_key_here"
    NVIDIA_MODEL: str = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "output"
    TEMP_DIR: str = "temp"
    WORKER_POLL_INTERVAL: int = 5

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Ensure target directories exist
for directory in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.TEMP_DIR]:
    os.makedirs(directory, exist_ok=True)

# reload
