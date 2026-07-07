import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/podcast"
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cpu"
    # NVIDIA NIM (primary LLM provider)
    NVIDIA_API_KEY: str = "your_nvidia_api_key_here"
    NVIDIA_MODEL: str = "openai/gpt-oss-120b"
    # OpenRouter (fallback LLM provider)
    OPENROUTER_API_KEY: str = "your_openrouter_api_key_here"
    OPENROUTER_MODEL: str = "google/gemma-4-31b-it:free"
    # Groq (fallback LLM provider)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    # Ollama (local LLM — highest priority when running)
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "qwen3:8b"
    OLLAMA_ENABLED: bool = False  # flip to True in .env to activate
    UPLOAD_DIR: str = "uploads"
    OUTPUT_DIR: str = "output"
    TEMP_DIR: str = "temp"
    WORKER_POLL_INTERVAL: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

# Ensure target directories exist
for directory in [settings.UPLOAD_DIR, settings.OUTPUT_DIR, settings.TEMP_DIR]:
    os.makedirs(directory, exist_ok=True)

# reload
