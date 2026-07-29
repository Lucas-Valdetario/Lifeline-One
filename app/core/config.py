from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração da aplicação, lida do ambiente/.env com valores padrão."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Aplicação ---------------------------------------------------------
    app_name: str = "Lifeline One — Agente Agendador"
    timezone: str = "America/Sao_Paulo"
    jwt_secret: str = "troque-esta-chave-em-producao"
    jwt_expires_minutes: int = 720
    admin_user: str = "admin"
    admin_password: str = "admin123"

    # --- Infra -------------------------------------------------------------
    database_url: str = "postgresql+psycopg2://lifeline:lifeline@db:5432/lifeline"
    redis_url: str = "redis://redis:6379/0"

    # --- Google AI (Gemini) ------------------------------------------------
    # Obrigatória: o agente depende do Gemini para entender o paciente, ler
    # comprovantes e transcrever áudios. Configure no .env.
    google_api_key: str
    gemini_text_model: str = "gemini-2.0-flash"
    # Modelo multimodal: também usado para transcrever áudio (app/services/audio.py).
    gemini_vision_model: str = "gemini-2.0-flash"

    @field_validator("google_api_key")
    @classmethod
    def _exige_chave_gemini(cls, value: str) -> str:
        """Falha cedo, com uma mensagem clara, se a chave do Gemini não vier configurada."""
        if not value.strip():
            raise ValueError(
                "GOOGLE_API_KEY é obrigatória. Gere uma chave em "
                "https://aistudio.google.com/apikey e configure-a no .env."
            )
        return value

    # --- Evolution API (WhatsApp) -----------------------------------------
    evolution_base_url: str = "http://evolution:8080"
    evolution_api_key: str = ""
    evolution_instance: str = "lifeline"

    # --- Regras de negócio da clínica -------------------------------------
    clinic_name: str = "Clínica Lifeline One"
    consultation_price: float = 380.00
    deposit_percent: float = 0.50
    pix_key: str = "financeiro@lifelineone.com.br"
    pix_holder: str = "Lifeline One Serviços Médicos LTDA"
    clinic_address: str = "SGAS 915, Lote 69, Bloco C, Sala 214 — Asa Sul, Brasília/DF"
    clinic_phone: str = "(61) 3333-1200"
    slot_hold_minutes: int = 30
    receipt_max_age_hours: int = 24

    @property
    def deposit_amount(self) -> float:
        """Valor do sinal (Pix), calculado a partir do preço da consulta."""
        return round(self.consultation_price * self.deposit_percent, 2)

    @property
    def whatsapp_enabled(self) -> bool:
        """True quando há credenciais da Evolution API configuradas."""
        return bool(self.evolution_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    """Instância única e cacheada das configurações."""
    return Settings()


settings = get_settings()
