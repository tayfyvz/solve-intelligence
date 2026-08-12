from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

import app.models as models
import app.schemas as schemas
from app.data import DOCUMENT_1, DOCUMENT_2
from app.db import Base, SessionLocal, engine, get_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Create the database tables
    Base.metadata.create_all(bind=engine)
    # Insert seed data
    with SessionLocal() as db:
        db.execute(insert(models.Document).values(id=1, content=DOCUMENT_1))
        db.execute(insert(models.Document).values(id=2, content=DOCUMENT_2))
        db.commit()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/document/{document_id}")
def get_document(document_id: int, db: Session = Depends(get_db)) -> schemas.DocumentRead:
    """Get a document from the database"""
    return db.scalar(select(models.Document).where(models.Document.id == document_id))


@app.post("/save/{document_id}")
def save(document_id: int, document: schemas.DocumentBase, db: Session = Depends(get_db)):
    """Save the document to the database"""
    db.execute(
        update(models.Document)
        .where(models.Document.id == document_id)
        .values(content=document.content)
    )
    db.commit()
    return {"document_id": document_id, "content": document.content}
