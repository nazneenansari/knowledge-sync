from fastapi import FastAPI, Request
from mangum import Mangum

from webhooks import router as webhooks_router

app = FastAPI()

app.include_router(webhooks_router)
# Create the Lambda handler
handler =  Mangum(app, lifespan="off")
