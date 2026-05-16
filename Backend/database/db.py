#db.py
from sqlalchemy import create_engine     #python to db
from sqlalchemy.ext.declarative  import declarative_base  #format sql tables
from sqlalchemy.orm import sessionmaker  #session boys
from dotenv import load_dotenv
import os


load_dotenv()

DATABASE_URL = ( 

    f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv("DB_PASSWORD")}"
    f"@{os.getenv("DB_HOST")}:{os.getenv("DB_PORT")}/{os.getenv("DB_NAME")}"  
   #mysql+pymysql://user:password@host:port/database_name
)

engine = create_engine(DATABASE_URL)  #connection sa db

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine) #pag create sa session 

Base = declarative_base()   #base class



def get_db():

    db = SessionLocal() #open session

    try:
        yield db

    finally:
        db.close()
