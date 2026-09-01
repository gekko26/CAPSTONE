#db.py
from sqlalchemy import create_engine     #python to db
from sqlalchemy.ext.declarative  import declarative_base  #format sql tables
from sqlalchemy.orm import sessionmaker  #session boys
from dotenv import load_dotenv
import os
from urllib.parse import quote_plus


load_dotenv()

DATABASE_URL = (

    f"mysql+pymysql://{quote_plus(os.getenv('DB_USER', ''))}:{quote_plus(os.getenv('DB_PASSWORD', ''))}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '3306')}/{os.getenv('DB_NAME', '')}"
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
