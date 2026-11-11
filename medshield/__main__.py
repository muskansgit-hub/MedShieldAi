"""`python -m medshield` starts the server on http://localhost:8000"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("medshield.api:app", host="127.0.0.1", port=8000)
