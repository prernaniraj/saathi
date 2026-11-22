import os
import uvicorn

from app.envfile import load_env_file

if __name__ == "__main__":
    load_env_file()
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", 3000)), access_log=False)
