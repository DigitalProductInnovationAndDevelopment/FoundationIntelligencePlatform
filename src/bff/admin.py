import os
import json
import subprocess
import datetime
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import Optional
from bff.auth import get_current_user_token
from bff.schemas import PipelineStatus, PipelineTrigger
from bff.utils.logging import logger

# Set up directories relative to project root
BFF_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(BFF_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

STATUS_FILE = os.path.join(SRC_DIR, "data", "pipeline_status.json")
LOG_FILE = os.path.join(SRC_DIR, "data", "pipeline_run.log")

router = APIRouter(
    prefix="/api/admin", 
    tags=["Administrative & Monitoring Data"],
    dependencies=[Depends(get_current_user_token)]
)

def read_status():
    """Reads execution status from JSON file."""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse status file: {e}")
    return {
        "status": "idle",
        "started_at": None,
        "finished_at": None,
        "last_run_source": None,
        "error": None
    }

def write_status(status: str, source: str, error: Optional[str] = None):
    """Writes current execution status to JSON file."""
    current = read_status()
    now = datetime.datetime.now().isoformat()
    
    current["status"] = status
    current["last_run_source"] = source
    
    if status == "running":
        current["started_at"] = now
        current["finished_at"] = None
        current["error"] = None
    else:
        current["finished_at"] = now
        if error:
            current["error"] = error
        else:
            current["error"] = None
            
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to write pipeline status: {e}")

def run_pipeline_task(mode: str):
    """Orchestrates pipeline execution as a background thread task."""
    write_status(status="running", source=mode)
    
    # Configure venv python path
    python_bin = os.path.join(PROJECT_ROOT, "venv", "bin", "python")
    if not os.path.exists(python_bin):
        # Fallback to system python if venv isn't found
        python_bin = "python"
        
    commands = []
    if mode == "quick_consolidate":
        commands.append([python_bin, "src/pipelines/run_pipeline.py", "--source", "consolidate", "--skip-contact-crawler"])
    elif mode == "refresh_charities":
        commands.append([python_bin, "src/pipelines/run_pipeline.py", "--source", "register_of_charities", "--limit", "5"])
        commands.append([python_bin, "src/pipelines/run_pipeline.py", "--source", "consolidate", "--skip-contact-crawler"])
    elif mode == "refresh_grants":
        commands.append([python_bin, "src/pipelines/run_pipeline.py", "--source", "360giving", "--limit", "5"])
        commands.append([python_bin, "src/pipelines/run_pipeline.py", "--source", "consolidate", "--skip-contact-crawler"])
        
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        
        with open(LOG_FILE, "w", encoding="utf-8") as log_f:
            for idx, cmd in enumerate(commands, 1):
                log_f.write(f"\n[{idx}/{len(commands)}] Running command: {' '.join(cmd)}\n")
                log_f.flush()
                
                process = subprocess.Popen(
                    cmd,
                    stdout=log_f,
                    stderr=log_f,
                    cwd=PROJECT_ROOT
                )
                process.wait()
                
                if process.returncode != 0:
                    raise Exception(f"Command '{' '.join(cmd)}' failed with exit code: {process.returncode}")
                    
        write_status(status="success", source=mode)
    except Exception as e:
        logger.error(f"Pipeline background execution error: {e}")
        write_status(status="failed", source=mode, error=str(e))


@router.get("/pipeline/status", response_model=PipelineStatus)
async def get_pipeline_status():
    """Retrieves current pipeline execution status."""
    return read_status()


@router.post("/pipeline/trigger", response_model=PipelineStatus)
async def trigger_pipeline(
    payload: PipelineTrigger,
    background_tasks: BackgroundTasks
):
    """Triggers specific pipeline run source dynamically in the background."""
    current_status = read_status()
    if current_status.get("status") == "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pipeline execution is already in progress."
        )
        
    if payload.source not in ["quick_consolidate", "refresh_charities", "refresh_grants"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported execution mode: {payload.source}"
        )
        
    background_tasks.add_task(run_pipeline_task, payload.source)
    
    return {
        "status": "running",
        "started_at": datetime.datetime.now().isoformat(),
        "finished_at": None,
        "last_run_source": payload.source,
        "error": None
    }


@router.get("/pipeline/logs")
async def get_pipeline_logs():
    """Returns the tail output (last 100 lines) of the execution log file."""
    if not os.path.exists(LOG_FILE):
        return {"logs": "No pipeline runs recorded yet."}
        
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            tail_lines = lines[-100:]
            return {"logs": "".join(tail_lines)}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to read execution logs: {e}"
        )
