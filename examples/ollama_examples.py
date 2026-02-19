"""
Ollama Examples - Real-world use cases for NIFTY Options Trading System
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.ollama_client import OllamaClient, ask, explain_concept, review_code


def example_1_quick_explanation():
    """Example 1: Quick concept explanation."""
    print("=" * 60)
    print("Example 1: Quick Concept Explanation")
    print("=" * 60)

    response = ask("What is swing detection in options trading? Answer in 2 sentences.")
    print(f"\nQuestion: What is swing detection?\n")
    print(f"Answer: {response}\n")


def example_2_strategy_documentation():
    """Example 2: Generate strategy documentation."""
    print("=" * 60)
    print("Example 2: Generate Strategy Documentation")
    print("=" * 60)

    client = OllamaClient("mistral")

    prompt = """
    Document this swing-break trading strategy:

    1. Data Pipeline: WebSocket ticks -> 1-min OHLCV bars + VWAP
    2. Swing Detection: Watch-based system (2 confirmations required)
    3. Filtration: Price 100-300 Rs, VWAP 4%+, SL 2-10%
    4. Order Entry: Proactive SL orders BEFORE swing breaks
    5. Position Tracking: R-based sizing, daily +/-5R exits

    Explain the key advantages of this approach vs traditional methods.
    Keep it concise (3-4 paragraphs).
    """

    system = "You are an expert in quantitative trading and algorithmic execution."

    print("\nGenerating strategy documentation...")
    response = client.generate(prompt, system=system, temperature=0.7)
    print(f"\n{response}\n")


def example_3_code_review():
    """Example 3: Code review assistance."""
    print("=" * 60)
    print("Example 3: Code Review - Order Placement Logic")
    print("=" * 60)

    # Sample code to review
    code_snippet = """
def place_entry_order(self, candidate):
    symbol = candidate['symbol']
    swing_low = candidate['swing_low']
    lots = candidate['lots']

    # Calculate entry order prices
    tick_size = 0.05
    trigger_price = swing_low - tick_size
    limit_price = trigger_price - 3

    # Place proactive SL order
    order = self.client.placeorder(
        strategy="baseline_v1",
        symbol=symbol,
        action="SELL",
        exchange="NFO",
        price_type="SL",
        trigger_price=trigger_price,
        price=limit_price,
        quantity=lots * 65,
        product="MIS"
    )

    return order
"""

    print("\nReviewing order placement code...")
    review = review_code(
        code_snippet,
        focus="potential bugs, edge cases, and error handling"
    )
    print(f"\n{review}\n")


def example_4_debugging_help():
    """Example 4: Get debugging help."""
    print("=" * 60)
    print("Example 4: Debugging Help")
    print("=" * 60)

    issue = """
    I'm seeing candidates get disqualified with 'SL>10%' rejections.

    Example:
    - Swing low: 150 Rs
    - Highest high: 167 Rs
    - SL price: 168 (highest_high + 1)
    - SL% calculation: (168 - 150) / 150 = 12%

    The filter rejects because SL% > 10% (MAX_SL_PERCENT).

    Questions:
    1. Is this the expected behavior?
    2. Should I adjust MAX_SL_PERCENT or change the SL calculation?
    3. What are the trade-offs?

    Provide a clear analysis and recommendation.
    """

    print("\nAsking for debugging help...")
    response = ask(
        issue,
        system="You are a trading system debugging expert with deep knowledge of risk management."
    )
    print(f"\n{response}\n")


def example_5_test_case_generation():
    """Example 5: Generate test cases."""
    print("=" * 60)
    print("Example 5: Test Case Generation")
    print("=" * 60)

    prompt = """
    Generate 5 test cases for swing detection edge cases:

    Swing Detection Rules:
    - Watch counters track confirmations (need 2 confirmations)
    - Swings must alternate: High -> Low -> High -> Low
    - Updates allowed if new extreme forms before alternation
    - Watch counter resets when swing is triggered

    Focus on edge cases that might break the system.
    Format as: Test Case N: [scenario] -> Expected: [outcome]
    """

    client = OllamaClient("mistral")

    print("\nGenerating test cases...")
    response = client.generate(prompt, temperature=0.8)
    print(f"\n{response}\n")


def example_6_explain_vwap():
    """Example 6: Explain complex concept."""
    print("=" * 60)
    print("Example 6: Explain VWAP Premium Calculation")
    print("=" * 60)

    explanation = explain_concept(
        "VWAP premium calculation in options trading and why 4% minimum is important"
    )
    print(f"\n{explanation}\n")


def example_7_chat_conversation():
    """Example 7: Multi-turn conversation."""
    print("=" * 60)
    print("Example 7: Multi-turn Conversation")
    print("=" * 60)

    client = OllamaClient("mistral")

    messages = [
        {
            "role": "system",
            "content": "You are a quantitative trading expert specializing in options strategies."
        },
        {
            "role": "user",
            "content": "What is R-multiple in trading?"
        }
    ]

    print("\nUser: What is R-multiple in trading?")
    response1 = client.chat(messages)
    print(f"Assistant: {response1}\n")

    # Continue conversation
    messages.append({"role": "assistant", "content": response1})
    messages.append({
        "role": "user",
        "content": "How do I use it for position sizing in options?"
    })

    print("User: How do I use it for position sizing in options?")
    response2 = client.chat(messages)
    print(f"Assistant: {response2}\n")


def main():
    """Run all examples."""
    print("\n")
    print("*" * 60)
    print("OLLAMA EXAMPLES - NIFTY Options Trading System")
    print("*" * 60)
    print("\n")

    client = OllamaClient()
    if not client.is_available():
        print("ERROR: Ollama server not running!")
        print("Start it with: C:\\Users\\rajat\\AppData\\Local\\Programs\\Ollama\\ollama.exe serve")
        return

    print("[OK] Ollama server is running\n")

    # Run examples (uncomment the ones you want to try)

    example_1_quick_explanation()
    # example_2_strategy_documentation()
    # example_3_code_review()
    # example_4_debugging_help()
    # example_5_test_case_generation()
    # example_6_explain_vwap()
    # example_7_chat_conversation()

    print("*" * 60)
    print("Examples complete! Uncomment others in main() to try them.")
    print("*" * 60)


if __name__ == "__main__":
    main()
