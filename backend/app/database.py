"""Optional PostgreSQL assessment history; calculations remain usable without a database."""
import os, json
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, DateTime, JSON, Integer, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase): pass
class Assessment(Base):
    __tablename__="assessments"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    ticker:Mapped[str]=mapped_column(String(24),index=True)
    market:Mapped[str]=mapped_column(String(8))
    horizon:Mapped[str]=mapped_column(String(32))
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),default=lambda:datetime.now(timezone.utc),index=True)
    payload:Mapped[dict]=mapped_column(JSON)

def db_session():
    url=os.getenv("DATABASE_URL")
    if not url: return None
    engine=create_engine(url,pool_pre_ping=True)
    return sessionmaker(bind=engine)()

def save_assessment(ticker:str,market:str,horizon:str,payload:dict):
    session=db_session()
    if session is None: return
    try:
        session.add(Assessment(ticker=ticker,market=market,horizon=horizon,payload=payload)); session.commit()
    finally: session.close()

def get_history(limit:int=100):
    session=db_session()
    if session is None: return None
    try:
        rows=session.scalars(select(Assessment).order_by(Assessment.created_at.desc()).limit(limit)).all()
        return [{"id":x.id,"ticker":x.ticker,"market":x.market,"horizon":x.horizon,"created_at":x.created_at.isoformat(),"payload":x.payload} for x in rows]
    finally: session.close()
