import os
from pathlib import Path
from urllib.parse import quote_plus
from dotenv import load_dotenv

ROOT = Path(__file__).parent.resolve()
load_dotenv(ROOT / ".env")

DB_HOST     = os.environ["DB_HOST"]
DB_USER     = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
DB_NAME     = os.environ.get("DB_NAME", "mydb")
DB_PORT     = os.environ.get("DB_PORT", "3306")

# Access code participants must enter before register_user will accept them.
QUIZ_ACCESS_CODE = os.environ["QUIZ_ACCESS_CODE"]

# Separate, admin-only code required by add_question so random participants
# can't add/modify quiz content through the public endpoint. Optional: if
# unset, add_question just reports itself as unconfigured instead of
# crashing the whole server on startup.
QUIZ_ADMIN_CODE = os.environ.get("QUIZ_ADMIN_CODE")

from sqlalchemy import create_engine

engine = create_engine(
    f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
    f"@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    pool_pre_ping=True,
    pool_recycle=3600,
)
