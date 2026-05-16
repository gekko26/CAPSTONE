#models.py
from sqlalchemy import Column, Integer, Float, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database.db import Base


class Subject(Base):
    __tablename__ = "subjects"

    id         = Column(Integer, primary_key=True, index=True)
    name       = Column(String(100), nullable=False)
    face_id    = Column(String(255), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    readings         = relationship("Reading",        back_populates="subject")
    training_data    = relationship("TrainingData",   back_populates="subject")
    deployment_logs  = relationship("DeploymentLog",  back_populates="subject")


class Reading(Base):
    __tablename__ = "sensor_readings"

    id          = Column(Integer, primary_key=True, index=True)
    subject_id  = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    temperature = Column(Float)
    humidity    = Column(Float)
    bac         = Column(Float)
    ear         = Column(Float)
    label       = Column(String(20))
    model_used  = Column(String(50))
    date        = Column(DateTime, server_default=func.now())

    subject = relationship("Subject", back_populates="readings")


class TrainingData(Base):
    __tablename__ = "training_data"

    id                   = Column(Integer, primary_key=True, index=True)
    date                 = Column(DateTime, server_default=func.now())
    subject_id           = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    mq3_1_max            = Column(Float)
    mq3_1_avg            = Column(Float)
    mq3_1_std            = Column(Float)
    mq3_2_max            = Column(Float)
    mq3_2_avg            = Column(Float)
    mq3_2_std            = Column(Float)
    mq3_3_max            = Column(Float)
    mq3_3_avg            = Column(Float)
    mq3_3_std            = Column(Float)
    rise_time            = Column(Float)
    decay_time           = Column(Float)
    spatial_variance     = Column(Float)   # spatial_variance_max
    spatial_variance_avg = Column(Float)   # ← new
    temperature          = Column(Float)
    humidity             = Column(Float)
    bac                  = Column(Float)
    label                = Column(Integer, nullable=True, default=-1)  # ← -1 = pending
    confidence           = Column(Float)

    subject = relationship("Subject", back_populates="training_data")


class DeploymentLog(Base):
    __tablename__ = "deployment_logs"

    id               = Column(Integer, primary_key=True, index=True)
    date             = Column(DateTime, server_default=func.now())
    subject_id       = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    mq3_1_max        = Column(Float)
    mq3_1_avg        = Column(Float)
    mq3_1_std        = Column(Float)
    mq3_2_max        = Column(Float)
    mq3_2_avg        = Column(Float)
    mq3_2_std        = Column(Float)
    mq3_3_max        = Column(Float)
    mq3_3_avg        = Column(Float)
    mq3_3_std        = Column(Float)
    rise_time        = Column(Float)
    decay_time       = Column(Float)
    spatial_variance = Column(Float)
    temperature      = Column(Float)
    humidity         = Column(Float)
    prediction       = Column(String(20))
    confidence       = Column(Float)
    risk_level       = Column(String(20))
    model_version    = Column(String(50))

    subject = relationship("Subject", back_populates="deployment_logs")


class User(Base):
    __tablename__ = "users"

    id           = Column(Integer, primary_key=True, index=True)
    username     = Column(String(50), unique=True, nullable=False)
    hashpassword = Column(String(255))
    created_at   = Column(DateTime, server_default=func.now())
    updated_at   = Column(DateTime, server_default=func.now())