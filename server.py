import os
from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get("/")
def home():
    return {"status": "Bot running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
