"""Configuration shared by the deletion audit application.

Evidence paths in the manifest are relative to DATASET_DIR.
"""
from pathlib import Path
import os

from dotenv import load_dotenv

CODE_DIR = Path(__file__).resolve().parent
REPO_ROOT = CODE_DIR.parent
_ENV_FILE = CODE_DIR / ".env"
load_dotenv(_ENV_FILE, override=False)

PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gpt-5")
CHEAP_MODEL = os.getenv("CHEAP_MODEL", "gpt-5-mini")
TEMPERATURE = 0.0
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "8000"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
DATASET_DIR = REPO_ROOT / "dataset"
CACHE_DIR = Path(os.getenv("CACHE_DIR", str(CODE_DIR / "cache"))).resolve()
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
API_KEY = os.getenv("OPENAI_API_KEY", "")
