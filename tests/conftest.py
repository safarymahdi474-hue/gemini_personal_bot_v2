import os, sys, asyncio
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
os.environ.setdefault("OWNER_ID", "1")
os.environ.setdefault("GEMINI_API_KEYS", "k1,k2")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
