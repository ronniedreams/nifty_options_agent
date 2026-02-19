# Ollama Integration Guide

## Installation Complete ✓

- Ollama v0.15.5 installed at: `C:\Users\rajat\AppData\Local\Programs\Ollama\`
- Mistral 7.2B model downloaded and ready
- Server running at: `http://localhost:11434`
- Python client utility created: `utils/ollama_client.py`

---

## Quick Start

### 1. Use from Python Code

```python
from utils.ollama_client import OllamaClient, ask, explain_concept

# Quick one-line queries
response = ask("What is swing detection?")
print(response)

# Explain trading concepts
explanation = explain_concept("VWAP premium")
print(explanation)

# Advanced usage with client
client = OllamaClient("mistral")
response = client.generate(
    prompt="Analyze this swing detection logic...",
    system="You are a quantitative trading expert",
    temperature=0.7
)
print(response)
```

### 2. Use in Your Trading System

#### Example: Generate Strategy Documentation

```python
# baseline_v1_live/utils/strategy_docs.py

from utils.ollama_client import OllamaClient

class StrategyDocumenter:
    def __init__(self):
        self.client = OllamaClient("mistral")

    def document_swing_detection(self):
        """Generate documentation for swing detection logic."""
        prompt = """
        Explain the watch-based swing detection system for NIFTY options:
        - Watch counters track higher highs/closes for swing lows
        - 2 confirmations required to trigger a swing
        - Swings must alternate: High → Low → High → Low
        - Updates allowed if new extreme forms before alternation

        Provide a clear technical explanation.
        """

        return self.client.generate(
            prompt=prompt,
            system="You are an expert in quantitative trading and technical analysis."
        )

    def review_order_logic(self):
        """Generate review of order placement logic."""
        prompt = """
        Review this order execution strategy:
        - Proactive SL orders placed BEFORE swing breaks
        - Entry: trigger = swing_low - 0.05, limit = trigger - 3
        - Exit SL: trigger = highest_high + 1, limit = trigger + 3

        Analyze advantages and potential risks.
        """

        return self.client.generate(prompt=prompt)

# Usage
documenter = StrategyDocumenter()
docs = documenter.document_swing_detection()
print(docs)
```

#### Example: Code Review Integration

```python
from utils.ollama_client import review_code

# Read your code
with open("baseline_v1_live/order_manager.py") as f:
    code = f.read()

# Get AI review
review = review_code(code, focus="potential bugs in order placement")
print(review)
```

#### Example: Debug Help

```python
from utils.ollama_client import ask

# Get help debugging an issue
issue = """
I'm getting candidates disqualified with 'SL>10%' rejections.
The swing low is 150 Rs, and highest high is 167 Rs.
SL price = 168 (highest_high + 1).
SL% = (168-150)/150 = 12%.

Why is this happening and how to fix it?
"""

response = ask(
    issue,
    system="You are a trading system debugging expert"
)
print(response)
```

---

## Available Models

Currently installed:
- **mistral:latest** (7.2B) - Fast, general purpose

### Download More Models

```bash
# From command line
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe pull llama2
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe pull codellama
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe pull neural-chat

# From Python
from utils.ollama_client import OllamaClient
client = OllamaClient()
client.pull_model("llama2")
```

### Recommended Models

| Model | Size | Best For |
|-------|------|----------|
| `mistral` | 7B | General purpose, fast responses |
| `llama2` | 7B/13B | Better reasoning, slower |
| `codellama` | 7B | Code generation and review |
| `neural-chat` | 7B | Detailed explanations |
| `phi` | 2.7B | Very fast, lightweight |

---

## Integration with Claude Code

You can now ask Claude Code to use Ollama in your requests:

```
> Use Ollama's mistral model to explain swing detection

> Generate test cases for order_manager.py using the local model

> Ask llama2 to review this code for potential issues

> Use Ollama to document the continuous filtration pipeline
```

Claude Code will use the `utils/ollama_client.py` utility to interact with Ollama.

---

## API Reference

### OllamaClient

```python
from utils.ollama_client import OllamaClient

client = OllamaClient(model="mistral", host="http://localhost:11434")

# Generate text
response = client.generate(
    prompt="Your prompt here",
    system="Optional system context",
    temperature=0.7,  # 0.0 = deterministic, 1.0 = creative
    max_tokens=500    # Optional limit
)

# Chat (with conversation history)
messages = [
    {"role": "system", "content": "You are a helpful assistant"},
    {"role": "user", "content": "What is VWAP?"},
    {"role": "assistant", "content": "VWAP is..."},
    {"role": "user", "content": "How is it calculated?"}
]
response = client.chat(messages, temperature=0.7)

# List available models
models = client.list_models()

# Download new model
client.pull_model("llama2")

# Check if server is running
if client.is_available():
    print("Ollama is ready!")
```

### Quick Functions

```python
from utils.ollama_client import ask, explain_concept, review_code

# Quick query
response = ask("What is swing trading?")

# Explain concept
explanation = explain_concept("stop loss percentage")

# Review code
with open("my_code.py") as f:
    code = f.read()
review = review_code(code, focus="performance")
```

---

## Server Management

### Start Server (Auto-starts on Windows)

```bash
# If not running, start manually:
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe serve
```

### Check Status

```bash
# Using curl
curl http://localhost:11434/api/tags

# Using Python
from utils.ollama_client import OllamaClient
client = OllamaClient()
if client.is_available():
    print("Server running!")
```

### List Models

```bash
# Command line
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe list

# Python
from utils.ollama_client import OllamaClient
client = OllamaClient()
models = client.list_models()
for model in models:
    print(f"{model['name']}: {model['size'] / 1e9:.1f} GB")
```

---

## Use Cases for Your Trading System

### 1. Strategy Documentation
Generate comprehensive docs for your swing detection, filtration, and order execution logic.

### 2. Code Review
Get AI-powered code reviews focusing on trading-specific issues like race conditions, order placement bugs, and position tracking.

### 3. Debugging Help
Ask Ollama to analyze error logs, suggest fixes, and explain complex behaviors.

### 4. Test Case Generation
Generate test scenarios for swing detection edge cases, filter boundary conditions, and order state transitions.

### 5. Configuration Optimization
Ask for suggestions on filter parameters, position sizing, and risk management settings.

### 6. Learning & Exploration
Understand complex concepts like VWAP calculation, swing alternation patterns, and R-multiple accounting.

---

## Performance Tips

### Faster Responses
- Use `mistral` or `phi` for quick queries
- Lower temperature (0.3-0.5) for deterministic answers
- Limit max_tokens for shorter responses

### Better Quality
- Use `llama2:13b` for complex reasoning
- Higher temperature (0.7-0.9) for creative solutions
- Provide detailed system context

### Memory Management
- Ollama uses ~8GB RAM for 7B models
- Close unused models: `ollama stop <model>`
- Monitor with Task Manager

---

## Troubleshooting

### Server Not Running
```bash
# Check if running
curl http://localhost:11434/api/tags

# Start manually
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe serve
```

### Model Not Found
```bash
# Pull the model
C:\Users\rajat\AppData\Local\Programs\Ollama\ollama.exe pull mistral
```

### Slow Responses
- Try smaller model: `phi` instead of `llama2:13b`
- Close other memory-intensive apps
- Use lower max_tokens

### Python Import Errors
```bash
# Install requests if missing
pip install requests
```

---

## Next Steps

1. **Try it out**: Run `python utils/ollama_client.py` to test
2. **Explore models**: Download and compare different models
3. **Integrate**: Use in your trading system for docs, review, and debugging
4. **Experiment**: Ask Claude Code to use Ollama for various tasks

For more info: https://ollama.ai/
