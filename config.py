import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / '.env')

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev')
    SQLALCHEMY_DATABASE_URI = os.getenv('DATABASE_URL') or 'sqlite:///bom.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SAP_BASE_URL = os.getenv('SAP_BASE_URL')
    SAP_USER = os.getenv('SAP_USER')
    SAP_PASSWORD = os.getenv('SAP_PASSWORD')
    SAP_RETRY = int(os.getenv('SAP_RETRY', '3'))

class ProdConfig(Config):
    pass

class DevConfig(Config):
    DEBUG = True

config = {
    'development': DevConfig,
    'production': ProdConfig,
}
