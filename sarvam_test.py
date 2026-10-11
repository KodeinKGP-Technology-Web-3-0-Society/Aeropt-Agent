import os
from pathlib import Path
from dotenv import load_dotenv
from sarvamai import SarvamAI

# Load environment variables from .env file
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

api_key = os.getenv("SARVAM_API_KEY")
if not api_key or not api_key.strip():
    raise SystemExit("Error: SARVAM_API_KEY is not set in .env")

client = SarvamAI(api_subscription_key=api_key)

response = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[
        {"role": "user", "content": "Hello! Introduce yourself briefly in one sentence."}
    ],
)

print(response.choices[0].message.content)
