"""Local entry point: python run.py. Configure keys before exposing beyond loopback."""
import uvicorn

from analytics_app.main import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
