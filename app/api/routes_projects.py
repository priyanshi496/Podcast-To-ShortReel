from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app import crud, schemas
from app.db import get_db

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.get("", response_model=List[schemas.ProjectSummary])
def list_projects(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    """Return all projects with video + clip counts for the Home screen."""
    projects = crud.get_projects(db, skip=skip, limit=limit)
    return [crud.get_project_summary(db, p) for p in projects]


@router.post("", response_model=schemas.ProjectResponse, status_code=201)
def create_project(body: schemas.ProjectCreate, db: Session = Depends(get_db)):
    return crud.create_project(db, body)


@router.get("/{project_id}", response_model=schemas.ProjectResponse)
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/{project_id}", response_model=schemas.ProjectResponse)
def update_project(project_id: int, body: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    project = crud.update_project(db, project_id, body)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, db: Session = Depends(get_db)):
    deleted = crud.delete_project(db, project_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Project not found")
