"""File upload endpoint."""
import os
import shutil
from fastapi import APIRouter, UploadFile, File, Request, HTTPException
from backend.config import UPLOAD_DIR, MAX_FILE_SIZE

router = APIRouter()


@router.post("/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Upload Excel file for processing."""
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx/.xls files are supported")

    # Save file
    filepath = os.path.join(UPLOAD_DIR, file.filename)
    with open(filepath, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_size = os.path.getsize(filepath)
    if file_size > MAX_FILE_SIZE:
        os.remove(filepath)
        raise HTTPException(400, f"File too large: {file_size / 1024 / 1024:.0f}MB > 500MB")

    return {
        "filename": file.filename,
        "filepath": filepath,
        "size_bytes": file_size,
        "size_mb": round(file_size / 1024 / 1024, 1),
    }
