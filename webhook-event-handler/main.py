from fastapi import FastAPI
from mangum import Mangum

from webhooks import router as webhooks_router
from dlq.routes import router as dlq_router

app = FastAPI()

app.include_router(webhooks_router)
app.include_router(dlq_router)

handler = Mangum(app, lifespan="off")
