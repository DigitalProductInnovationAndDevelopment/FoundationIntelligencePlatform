import os
import json
import subprocess
import datetime
import tempfile
import time
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from typing import Optional, List
from bff.auth import get_current_user_token
from bff.schemas import PipelineStatus, PipelineTrigger
from bff.utils.logging import logger

# Set up directories relative to project root
BFF_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(BFF_DIR)
PROJECT_ROOT = os.path.dirname(SRC_DIR)

STATUS_FILE = os.path.join(SRC_DIR, "data", "pipeline_status.json")
LOG_FILE = os.path.join(SRC_DIR, "data", "pipeline_run.log")
LOCK_FILE = os.path.join(SRC_DIR, "data", "pipeline_run.lock")
LOCK_STALE_AFTER_SECONDS = 6 * 60 * 60

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


def _atomic_json_write(path: str, payload: dict):
    """Persist status without exposing readers to partially written JSON."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix=".pipeline-status-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=4)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise


def _read_lock_metadata():
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _lock_owner_is_alive(metadata: dict) -> bool:
    pid = metadata.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Do not reclaim a lock merely because another valid user owns it.
        return True
    return True


def claim_pipeline_run(mode: str) -> bool:
    """Acquire the process-wide pipeline claim or report that one is active."""
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    metadata = {
        "pid": os.getpid(),
        "mode": mode,
        "claimed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    for _ in range(2):
        try:
            descriptor = os.open(LOCK_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = _read_lock_metadata()
            try:
                age_seconds = max(0.0, time.time() - os.path.getmtime(LOCK_FILE))
            except OSError:
                continue
            if age_seconds < LOCK_STALE_AFTER_SECONDS or _lock_owner_is_alive(existing):
                return False
            # A stale lock is reclaimed only when it is old and its owner is
            # conclusively gone (or the metadata is unusable).
            try:
                os.unlink(LOCK_FILE)
            except FileNotFoundError:
                continue
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle)
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except Exception:
            if os.path.exists(LOCK_FILE):
                os.unlink(LOCK_FILE)
            raise
    return False


def release_pipeline_claim():
    """Release this process's claim; never remove a newer owner's lock."""
    metadata = _read_lock_metadata()
    if metadata.get("pid") != os.getpid():
        return
    try:
        os.unlink(LOCK_FILE)
    except FileNotFoundError:
        pass


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
        _atomic_json_write(STATUS_FILE, current)
    except Exception as e:
        logger.error(f"Failed to write pipeline status: {e}")

def run_pipeline_task(
    mode: str,
    limit: Optional[int] = None,
    fresh: bool = False,
    search_term: Optional[str] = None,
    reg_numbers: Optional[List[int]] = None,
    skip_contact_crawler: bool = False,
    claim_acquired: bool = False,
):
    """Orchestrates pipeline execution as a background thread task."""
    if not claim_acquired and not claim_pipeline_run(mode):
        logger.warning("Skipped duplicate pipeline task for %s because a run is already claimed.", mode)
        return
    write_status(status="running", source=mode)
    
    # Configure venv python path
    python_bin = os.path.join(PROJECT_ROOT, "venv", "bin", "python")
    if not os.path.exists(python_bin):
        # Fallback to system python if venv isn't found
        python_bin = "python"
        
    commands = []
    
    # Helper to build scraper commands with shared args
    def build_scraper_cmd(scraper_source):
        cmd = [python_bin, "src/pipelines/run_pipeline.py", "--source", scraper_source]
        if limit:
            cmd += ["--limit", str(limit)]
        if fresh:
            cmd += ["--fresh"]
        return cmd

    # Helper to build consolidate command
    def build_consolidate_cmd():
        cmd = [python_bin, "src/pipelines/run_pipeline.py", "--source", "consolidate"]
        if skip_contact_crawler:
            cmd += ["--skip-contact-crawler"]
        return cmd

    if mode == "quick_consolidate":
        commands.append(build_consolidate_cmd())
    elif mode == "refresh_charities":
        cmd = build_scraper_cmd("register_of_charities")
        if search_term:
            cmd += ["--search", search_term]
        if reg_numbers:
            cmd += ["--reg-numbers"] + [str(n) for n in reg_numbers]
        commands.append(cmd)
        commands.append(build_consolidate_cmd())
    elif mode == "refresh_grants":
        cmd = build_scraper_cmd("360giving")
        commands.append(cmd)
        commands.append(build_consolidate_cmd())
    elif mode == "full_run":
        cmd = build_scraper_cmd("full_run")
        if search_term:
            cmd += ["--search", search_term]
        if reg_numbers:
            cmd += ["--reg-numbers"] + [str(n) for n in reg_numbers]
        if skip_contact_crawler:
            cmd += ["--skip-contact-crawler"]
        commands.append(cmd)
        
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
    finally:
        release_pipeline_claim()


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
    if payload.source not in ["quick_consolidate", "refresh_charities", "refresh_grants", "full_run"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported execution mode: {payload.source}"
        )
    if not claim_pipeline_run(payload.source):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline execution is already in progress.",
        )
    try:
        write_status(status="running", source=payload.source)
    except Exception:
        release_pipeline_claim()
        raise
        
    background_tasks.add_task(
        run_pipeline_task,
        payload.source,
        payload.limit,
        payload.fresh or False,
        payload.search_term,
        payload.reg_numbers,
        payload.skip_contact_crawler or False,
        True,
    )
    
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
