import uvicorn
from .config import CONFIG

if __name__ == "__main__":
    uvicorn.run("aurix.main:app", host=CONFIG["host"], port=CONFIG["port"], log_level="info")
