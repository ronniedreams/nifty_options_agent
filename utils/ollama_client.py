"""
Ollama Client - Easy integration with local Ollama models
Provides simple API for generating text using local LLMs
"""

import requests
import json
from typing import Optional, Dict, List


class OllamaClient:
    """Client for interacting with local Ollama server."""

    def __init__(self, model: str = "mistral", host: str = "http://localhost:11434"):
        """
        Initialize Ollama client.

        Args:
            model: Model name to use (default: mistral)
            host: Ollama server URL (default: http://localhost:11434)
        """
        self.model = model
        self.host = host

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Generate text using Ollama model.

        Args:
            prompt: The prompt to generate from
            system: Optional system message (context/role)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate

        Returns:
            Generated text response

        Example:
            >>> client = OllamaClient("mistral")
            >>> response = client.generate(
            ...     "Explain swing detection",
            ...     system="You are a quantitative trading expert"
            ... )
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }

        if system:
            payload["system"] = system

        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            response = requests.post(
                f"{self.host}/api/generate",
                json=payload,
                timeout=120
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Ollama API error: {e}")

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7
    ) -> str:
        """
        Chat with Ollama using conversation history.

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature

        Returns:
            Assistant's response

        Example:
            >>> client = OllamaClient("mistral")
            >>> messages = [
            ...     {"role": "system", "content": "You are a trading expert"},
            ...     {"role": "user", "content": "What is a swing low?"}
            ... ]
            >>> response = client.chat(messages)
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }

        try:
            response = requests.post(
                f"{self.host}/api/chat",
                json=payload,
                timeout=120
            )
            response.raise_for_status()
            result = response.json()
            return result.get("message", {}).get("content", "")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Ollama chat API error: {e}")

    def list_models(self) -> List[Dict]:
        """
        List available Ollama models.

        Returns:
            List of model information dicts
        """
        try:
            response = requests.get(f"{self.host}/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to list models: {e}")

    def pull_model(self, model: str) -> None:
        """
        Download a new model.

        Args:
            model: Model name to download (e.g., "llama2", "codellama")
        """
        try:
            response = requests.post(
                f"{self.host}/api/pull",
                json={"name": model},
                timeout=600  # 10 minute timeout for downloads
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"Failed to pull model: {e}")

    def is_available(self) -> bool:
        """
        Check if Ollama server is running and accessible.

        Returns:
            True if server is available, False otherwise
        """
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False


# Convenience functions for quick use
def ask(prompt: str, model: str = "mistral", system: Optional[str] = None) -> str:
    """
    Quick one-off query to Ollama.

    Args:
        prompt: Question or prompt
        model: Model to use
        system: Optional system context

    Returns:
        Model's response

    Example:
        >>> from utils.ollama_client import ask
        >>> response = ask("What is VWAP?")
    """
    client = OllamaClient(model)
    return client.generate(prompt, system=system)


def review_code(code: str, focus: Optional[str] = None) -> str:
    """
    Ask Ollama to review code.

    Args:
        code: Code to review
        focus: Optional focus area (e.g., "performance", "security")

    Returns:
        Code review response

    Example:
        >>> with open("order_manager.py") as f:
        ...     code = f.read()
        >>> review = review_code(code, focus="potential bugs")
    """
    system = "You are an expert code reviewer specializing in Python and trading systems."

    prompt = f"Review this code"
    if focus:
        prompt += f" focusing on {focus}"
    prompt += f":\n\n```python\n{code}\n```"

    return ask(prompt, system=system)


def explain_concept(concept: str) -> str:
    """
    Ask Ollama to explain a trading concept.

    Args:
        concept: The concept to explain

    Returns:
        Explanation

    Example:
        >>> explanation = explain_concept("swing detection")
    """
    system = "You are a quantitative trading expert. Explain concepts clearly and concisely."
    prompt = f"Explain this trading concept: {concept}"
    return ask(prompt, system=system)


if __name__ == "__main__":
    # Test the client
    print("Testing Ollama Client...")

    client = OllamaClient("mistral")

    # Check availability
    if not client.is_available():
        print("ERROR: Ollama server not running!")
        print("Start it with: ollama serve")
        exit(1)

    print("[OK] Ollama server is running")

    # List models
    models = client.list_models()
    print(f"[OK] Available models: {', '.join([m['name'] for m in models])}")

    # Test generation
    print("\nTesting generation...")
    response = client.generate(
        "What is swing detection in options trading? Answer in 2 sentences.",
        system="You are a quantitative trading expert."
    )
    print(f"Response: {response}")

    # Test chat
    print("\nTesting chat...")
    messages = [
        {"role": "system", "content": "You are a helpful trading assistant."},
        {"role": "user", "content": "What is VWAP?"}
    ]
    chat_response = client.chat(messages)
    print(f"Chat response: {chat_response}")

    print("\n[OK] All tests passed!")
