"""LLM integration with multi-provider support."""
import os
from typing import Optional, Any
from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI


class LLMProvider:
    """Manages LLM provider initialization and model selection."""

    @staticmethod
    def get_model(
        model: str = "gpt-4.1-mini",
        provider: str = "openai",
        temperature: float = 0.7,
    ) -> Any:
        """
        Get an LLM model instance based on provider and model name.
        
        Args:
            model: Model identifier
            provider: Provider name (openai, anthropic, google, xai, ollama)
            temperature: Temperature for generation
            
        Returns:
            LLM instance
        """
        if provider == "openai":
            return ChatOpenAI(
                model=model,
                temperature=temperature,
                api_key=os.getenv("OPENAI_API_KEY"),
                streaming=True,
            )
        elif provider == "anthropic":
            return ChatAnthropic(
                model=model,
                temperature=temperature,
                api_key=os.getenv("ANTHROPIC_API_KEY"),
                streaming=True,
            )
        elif provider == "google":
            return ChatGoogleGenerativeAI(
                model=model,
                temperature=temperature,
                api_key=os.getenv("GOOGLE_API_KEY"),
                streaming=True,
            )
        elif provider == "xai":
            # xAI uses OpenAI-compatible API
            return ChatOpenAI(
                model=model,
                base_url="https://api.x.ai/v1",
                api_key=os.getenv("XAI_API_KEY"),
                temperature=temperature,
                streaming=True,
            )
        elif provider == "ollama":
            from langchain_ollama import ChatOllama
            return ChatOllama(
                model=model,
                base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
                temperature=temperature,
            )
        else:
            raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def get_fast_model(
        model: str = "gpt-4.1-nano",
        provider: str = "openai",
    ) -> Any:
        """Get a fast model for quick operations (planning, validation)."""
        return LLMProvider.get_model(model, provider, temperature=0.3)

    @staticmethod
    def list_models(provider: str = "openai") -> list[str]:
        """List available models for a provider."""
        models = {
            "openai": ["gpt-4.1-mini", "gpt-4.1-nano", "gpt-4-turbo", "gpt-3.5-turbo"],
            "anthropic": ["claude-3-sonnet-20240229", "claude-3-opus-20240229"],
            "google": ["gemini-2.5-flash", "gemini-pro"],
            "xai": ["grok-2", "grok-3"],
            "ollama": ["llama2", "mistral", "neural-chat"],
        }
        return models.get(provider, [])
