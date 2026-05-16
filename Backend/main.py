from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database.db import engine
from database import models
from routes import sensor, readings,camera,predict,recognition,training

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="PAKYU KAYO")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "AlcoDetect API running"}

app.include_router(sensor.router)
app.include_router(readings.router)
app.include_router(camera.router)
app.include_router(recognition.router)
app.include_router(predict.router)
app.include_router(training.router)