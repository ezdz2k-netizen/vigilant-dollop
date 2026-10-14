
"""
MT5 DUAL AI TRADING ANALYSIS ENGINE
==================================
Stage 1: Fast AI market screening
Stage 2: Deep AI market review

Features:
- MetaTrader 5 market data
- Existing Bollinger Bands / ADX / EMA indicator engine
- Two-stage AI review
- Simulation mode without API credits
- Optional forced signal for workflow testing
- CSV analysis journal
- No trading order execution

IMPORTANT:
Simulation results are NOT real AI analysis.
Forced signals are NOT genuine trading signals.
This application NEVER places trades.
"""

import os
import json
import csv
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import MetaTrader5 as mt5

from dotenv import load_dotenv
from openai import OpenAI

from indicators import calculate_indicators, generate_signal


# ============================================================
# 1. PROJECT CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

ENV_FILE = BASE_DIR / ".env"

load_dotenv(
    dotenv_path=ENV_FILE,
    override=True
)

SYMBOL = os.getenv("MT5_SYMBOL", "EURUSD")

FAST_MODEL = os.getenv(
    "OPENAI_FAST_MODEL",
    "gpt-6-luna"
)

DEEP_MODEL = os.getenv(
    "OPENAI_DEEP_MODEL",
    "gpt-6.1-sol"
)

TIMEFRAME = mt5.TIMEFRAME_M15

BARS_TO_FETCH = 250

# ------------------------------------------------------------
# TESTING SETTINGS
# ------------------------------------------------------------

# True = No OpenAI API calls
# False = Real OpenAI API requests
SIMULATION_MODE = True

# True = Artificial BUY candidate for testing both AI stages
# False = Use actual indicator-generated signals
FORCE_TEST_SIGNAL = False

# Trading execution is NOT implemented.
ANALYSIS_ONLY = True

# Separate simulation results from genuine AI assessments.
SIMULATION_JOURNAL = BASE_DIR / "ai_simulation_journal.csv"
LIVE_JOURNAL = BASE_DIR / "ai_signal_journal.csv"


# ============================================================
# 2. STRUCTURED AI RESPONSE SCHEMA
# ============================================================

AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"]
        },
        "approved": {
            "type": "boolean"
        },
        "confidence": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100
        },
        "market_condition": {
            "type": "string"
        },
        "reason": {
            "type": "string"
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "string"
            }
        }
    },
    "required": [
        "action",
        "approved",
        "confidence",
        "market_condition",
        "reason",
        "risks"
    ],
    "additionalProperties": False
}


# ============================================================
# 3. AI SYSTEM PROMPTS
# ============================================================

FAST_PROMPT = """
You are the fast screening engine for a research-only
forex trading analysis system.

Evaluate the provided technical indicators.

Your responsibilities:

1. Check Bollinger Band breakout strength.
2. Evaluate ADX trend strength.
3. Compare +DI and -DI.
4. Confirm price direction relative to EMA50.
5. Check tick volume against its average.
6. Identify possible false breakouts.
7. Identify conflicting signals.

Decision rules:

- Approve only when the candidate direction is supported.
- Never reverse the original candidate direction.
- If uncertain, return HOLD and approved=false.
- Do not approve an original HOLD signal.
- Do not invent missing information.

Confidence is a subjective analytical assessment,
not a probability of profitability.

Output must follow the supplied JSON schema.

This is research only.
Never provide trade execution instructions.
"""


DEEP_PROMPT = """
You are the independent deep analysis engine for a
research-only forex trading system.

You receive:

1. Market data and technical indicators.
2. The original rule-based candidate.
3. The first-stage AI assessment.

Your responsibilities:

1. Independently review the original candidate.
2. Evaluate overall trend direction.
3. Analyze Bollinger Band breakout quality.
4. Evaluate ADX and directional indicators.
5. Check EMA50 trend alignment.
6. Assess volume confirmation.
7. Identify contradictory technical evidence.
8. Assess possible false-breakout risk.
9. Identify limitations in the supplied market data.

Do not automatically agree with the first model.

Approve only if the original candidate direction
is supported by the evidence.

If evidence is weak, conflicting, or incomplete,
return HOLD and approved=false.

Never reverse the original candidate direction.

Do not invent market data.

Confidence is NOT a calibrated win probability.

Output must follow the supplied JSON schema.

Never place trades or provide execution instructions.
"""


# ============================================================
# 4. MT5 MARKET DATA ENGINE
# ============================================================

def get_market_snapshot():

    print("\nConnecting to MetaTrader 5...")

    if not mt5.initialize():
        raise RuntimeError(
            f"MT5 initialization failed: {mt5.last_error()}"
        )

    try:

        account = mt5.account_info()

        if account is None:
            raise RuntimeError(
                "Unable to retrieve MT5 account information"
            )

        is_demo = (
            account.trade_mode
            == mt5.ACCOUNT_TRADE_MODE_DEMO
        )

        print("MT5 connected successfully")
        print("Account type:", "DEMO" if is_demo else "LIVE")

        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(
                f"Unable to select symbol: {SYMBOL}"
            )

        # Position 1 excludes the currently forming candle.
        rates = mt5.copy_rates_from_pos(
            SYMBOL,
            TIMEFRAME,
            1,
            BARS_TO_FETCH
        )

        if rates is None or len(rates) < 100:
            raise RuntimeError(
                f"Insufficient MT5 candle data: {mt5.last_error()}"
            )

        df = pd.DataFrame(rates)

        df["time"] = pd.to_datetime(
            df["time"],
            unit="s",
            utc=True
        )

        df = calculate_indicators(df)

        rule_action, rule_reason = generate_signal(df)

        fields = [
            "time",
            "open",
            "high",
            "low",
            "close",
            "bb_upper",
            "bb_middle",
            "bb_lower",
            "ema50",
            "adx",
            "plus_di",
            "minus_di",
            "tick_volume",
            "volume_avg"
        ]

        recent = df[fields].tail(10).copy()

        if recent.isna().any().any():
            raise ValueError(
                "Incomplete indicator data"
            )

        recent["time"] = recent["time"].astype(str)

        last = df.iloc[-1]

        snapshot = {
            "symbol": SYMBOL,
            "timeframe": "M15",
            "last_closed_candle": str(last["time"]),
            "close": float(last["close"]),
            "rule_action": rule_action,
            "rule_reason": rule_reason,
            "actual_rule_action": rule_action,
            "is_forced_test": False,
            "account_is_demo": is_demo,
            "candles": recent.to_dict(orient="records")
        }

        return snapshot

    finally:
        mt5.shutdown()
        print("MT5 data connection closed.")


# ============================================================
# 5. SIMULATION ENGINE
# ============================================================

def simulate_ai_response(model, payload):

    print(f"[SIMULATION] Generating mock result for {model}")

    market = payload.get("market", payload)

    candidate = market.get(
        "rule_action",
        "HOLD"
    )

    # This is a mock workflow response.
    # It does NOT analyze indicators.
    approved = candidate in ("BUY", "SELL")

    if approved:
        action = candidate
        confidence = 75
    else:
        action = "HOLD"
        confidence = 0

    return {
        "action": action,
        "approved": approved,
        "confidence": confidence,
        "market_condition": "SIMULATED WORKFLOW TEST",
        "reason": (
            "Mock AI assessment used only to test "
            "the two-stage system. No genuine "
            "market analysis was performed."
        ),
        "risks": [
            "Simulated output",
            "Not an actual AI trading signal",
            "Not suitable for performance evaluation"
        ]
    }


# ============================================================
# 6. REAL OPENAI REQUEST ENGINE
# ============================================================

def ask_model(model, instructions, payload, effort):

    if SIMULATION_MODE:
        return simulate_ai_response(
            model,
            payload
        )

    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY missing from .env"
        )

    # Create client only when real API calls are required.
    client = OpenAI(
        api_key=api_key,
        timeout=60.0,
        max_retries=2
    )

    response = client.responses.create(
        model=model,
        reasoning={
            "effort": effort
        },
        instructions=instructions,
        input=json.dumps(
            payload,
            default=str,
            allow_nan=False
        ),
        text={
            "format": {
                "type": "json_schema",
                "name": "market_assessment",
                "strict": True,
                "schema": AI_RESPONSE_SCHEMA
            }
        }
    )

    content = response.output_text

    if not content:
        raise ValueError(
            f"No structured response received from {model}"
        )

    result = json.loads(content)

    validate_response(result)

    return result


# ============================================================
# 7. VALIDATION AND SAFETY GATES
# ============================================================

def validate_response(result):

    if not isinstance(result, dict):
        raise ValueError("AI response must be an object")

    if result.get("action") not in [
        "BUY",
        "SELL",
        "HOLD"
    ]:
        raise ValueError("Invalid AI action")

    if type(result.get("approved")) is not bool:
        raise ValueError("Invalid approval field")

    confidence = result.get("confidence")

    if (
        type(confidence) is not int
        or not 0 <= confidence <= 100
    ):
        raise ValueError("Invalid confidence value")

    if not isinstance(result.get("reason"), str):
        raise ValueError("Invalid reason")

    if not isinstance(
        result.get("market_condition"),
        str
    ):
        raise ValueError("Invalid market condition")

    risks = result.get("risks")

    if (
        not isinstance(risks, list)
        or not all(isinstance(r, str) for r in risks)
    ):
        raise ValueError("Invalid risks list")


def reject_result(reason):

    return {
        "action": "HOLD",
        "approved": False,
        "confidence": 0,
        "market_condition": "NOT APPROVED",
        "reason": reason,
        "risks": []
    }


def enforce_gate(result, expected_action):

    validate_response(result)

    if (
        result["action"] != expected_action
        or result["approved"] is not True
    ):
        return reject_result(
            "Candidate rejected by the analysis gate: "
            + result["reason"]
        )

    return result


# ============================================================
# 8. CSV SIGNAL JOURNAL
# ============================================================

def save_journal(snapshot, fast, deep, final):

    journal = (
        SIMULATION_JOURNAL
        if SIMULATION_MODE
        else LIVE_JOURNAL
    )

    row = {
        "logged_at_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "symbol": snapshot["symbol"],
        "timeframe": snapshot["timeframe"],

        "candle_time": snapshot["last_closed_candle"],

        "close": snapshot["close"],

        "simulation_mode": SIMULATION_MODE,
        "forced_test": snapshot["is_forced_test"],

        "original_rule_signal":
            snapshot["actual_rule_action"],

        "candidate_signal":
            snapshot["rule_action"],

        "fast_model": FAST_MODEL,
        "fast_action": (
            fast["action"] if fast else "SKIPPED"
        ),

        "deep_model": DEEP_MODEL,
        "deep_action": (
            deep["action"] if deep else "SKIPPED"
        ),

        "final_action": final["action"],
        "approved": final["approved"],
        "confidence": final["confidence"],

        "market_condition": final["market_condition"],

        "reason": final["reason"],

        "risks": json.dumps(final["risks"])
    }

    exists = journal.exists()

    with journal.open(
        "a",
        newline="",
        encoding="utf-8"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=row.keys()
        )

        if not exists:
            writer.writeheader()

        writer.writerow(row)

    print("\nJournal saved:", journal)


# ============================================================
# 9. MAIN DUAL AI WORKFLOW
# ============================================================

def main():

    print("\n================================")
    print("       DUAL AI MT5 ENGINE       ")
    print("================================")

    print("MODE: ANALYSIS ONLY")

    if SIMULATION_MODE:
        print("AI MODE: SIMULATION")
    else:
        print("AI MODE: REAL OPENAI API")

    print("Fast model:", FAST_MODEL)
    print("Deep model:", DEEP_MODEL)

    snapshot = get_market_snapshot()

    # Artificial candidate only for simulation testing.
    if SIMULATION_MODE and FORCE_TEST_SIGNAL:

        snapshot["rule_action"] = "BUY"

        snapshot["rule_reason"] = (
            "FORCED TEST CANDIDATE - NOT A REAL SIGNAL"
        )

        snapshot["is_forced_test"] = True

        print("\nWARNING: FORCED TEST SIGNAL ENABLED")

    print("\n===== MARKET SNAPSHOT =====")

    print("Symbol:", snapshot["symbol"])
    print("Timeframe:", snapshot["timeframe"])
    print("Candle:", snapshot["last_closed_candle"])
    print("Close:", snapshot["close"])

    print(
        "Original rule signal:",
        snapshot["actual_rule_action"]
    )

    print(
        "Candidate signal:",
        snapshot["rule_action"]
    )

    print("Reason:", snapshot["rule_reason"])

    fast = None
    deep = None

    # --------------------------------------------------------
    # RULE-BASED FIRST GATE
    # --------------------------------------------------------

    if snapshot["rule_action"] == "HOLD":

        print("\nNo BUY or SELL candidate detected.")
        print("Skipping both AI models.")

        final = reject_result(
            "Rule-based engine found no new setup"
        )

    else:

        # ----------------------------------------------------
        # STAGE 1: FAST MODEL
        # ----------------------------------------------------

        print("\n================================")
        print("[1/2] FAST AI SCREENING")
        print("================================")

        fast = ask_model(
            model=FAST_MODEL,
            instructions=FAST_PROMPT,
            payload=snapshot,
            effort="low"
        )

        fast = enforce_gate(
            fast,
            snapshot["rule_action"]
        )

        print("Model:", FAST_MODEL)
        print("Action:", fast["action"])
        print("Approved:", fast["approved"])
        print("Confidence:", fast["confidence"])
        print("Reason:", fast["reason"])

        # ----------------------------------------------------
        # STAGE 2: DEEP MODEL
        # ----------------------------------------------------

        if not fast["approved"]:

            print("\nFast model rejected candidate.")
            print("Skipping deep model.")

            final = fast

        else:

            print("\n================================")
            print("[2/2] DEEP AI ANALYSIS")
            print("================================")

            deep_payload = {
                "market": snapshot,
                "stage_one": fast
            }

            deep = ask_model(
                model=DEEP_MODEL,
                instructions=DEEP_PROMPT,
                payload=deep_payload,
                effort="medium"
            )

            deep = enforce_gate(
                deep,
                snapshot["rule_action"]
            )

            print("Model:", DEEP_MODEL)
            print("Action:", deep["action"])
            print("Approved:", deep["approved"])
            print("Confidence:", deep["confidence"])
            print("Reason:", deep["reason"])

            final = deep

    # --------------------------------------------------------
    # FINAL RESULT
    # --------------------------------------------------------

    print("\n================================")
    print("       FINAL ASSESSMENT         ")
    print("================================")

    print("Action:", final["action"])
    print("Approved:", final["approved"])
    print("Confidence:", final["confidence"])

    print(
        "Market condition:",
        final["market_condition"]
    )

    print("Reason:", final["reason"])

    print("\nRisks:")

    for risk in final["risks"]:
        print("-", risk)

    if not final["risks"]:
        print("- None reported")

    # --------------------------------------------------------
    # SAVE JOURNAL
    # --------------------------------------------------------

    save_journal(
        snapshot,
        fast,
        deep,
        final
    )

    print("\n================================")

    if SIMULATION_MODE:
        print("SIMULATED RESULTS - NOT REAL AI ANALYSIS")

    if snapshot["is_forced_test"]:
        print("ARTIFICIAL CANDIDATE - NOT A REAL SIGNAL")

    print("NO TRADE EXECUTED")
    print("================================")


# ============================================================
# 10. APPLICATION ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print("\n================================")
        print("ANALYSIS STOPPED SAFELY")
        print("================================")

        print("Error:", error)
        print("NO TRADE EXECUTED")
