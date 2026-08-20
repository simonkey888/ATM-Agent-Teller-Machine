from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VideoWorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoJob:
    source_video: Path
    output_video: Path
    provenance_file: Path
    required_duration_seconds: float
    duration_tolerance_seconds: float = 0.35
    width: int = 720
    height: int = 1280
    require_audio: bool = True
    require_subtitles: bool = True
    max_runtime_seconds: int = 600
    staging_root: Path | None = None


def _regular_local(path: Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise VideoWorkerError("video input must be one regular local file")
    return resolved


def _provenance(path: Path, source: Path) -> dict[str, Any]:
    data = json.loads(_regular_local(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("rights") not in {"OWNER_PROVIDED", "CC0"}:
        raise VideoWorkerError("local asset rights provenance is absent or unsupported")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    if str(data.get("source_sha256") or "").lower() != expected:
        raise VideoWorkerError("provenance hash does not match source media")
    return data


class VideoShortFormWorker:
    """Small MoneyPrinterTurbo-inspired adapter: local assets + FFmpeg, no WebUI/APIs/posting."""

    def __init__(self, *, ffmpeg: str | None = None, ffprobe: str | None = None):
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = ffprobe or shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            raise VideoWorkerError("pinned worker requires ffmpeg and ffprobe")

    def assemble(self, job: VideoJob) -> dict[str, Any]:
        if job.staging_root is None:
            raise VideoWorkerError("video job requires an explicit staging root")
        staging_root = Path(job.staging_root).resolve()
        if not staging_root.is_dir() or staging_root.is_symlink():
            raise VideoWorkerError("video staging root must be one real directory")
        source = _regular_local(job.source_video)
        provenance_path = _regular_local(job.provenance_file)
        output = Path(job.output_video).resolve()
        for candidate in (source, provenance_path, output):
            try:
                candidate.relative_to(staging_root)
            except ValueError as exc:
                raise VideoWorkerError("video paths must remain inside staging root") from exc
        provenance = _provenance(provenance_path, source)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and (output.is_symlink() or not output.is_file()):
            raise VideoWorkerError("video output must be a regular file or absent")
        vf = (
            f"scale={job.width}:{job.height}:force_original_aspect_ratio=decrease,"
            f"pad={job.width}:{job.height}:(ow-iw)/2:(oh-ih)/2:black,format=yuv420p"
        )
        command = [
            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-protocol_whitelist", "file,pipe", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a?", "-map", "0:s?",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart",
        ]
        if job.require_audio:
            command += ["-c:a", "aac"]
        else:
            command += ["-an"]
        if job.require_subtitles:
            command += ["-c:s", "mov_text"]
        command += [str(output)]
        worker_env = {"PATH": "/usr/bin:/bin", "HOME": str(staging_root), "TMPDIR": str(staging_root)}
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=job.max_runtime_seconds,
            cwd=staging_root,
            env=worker_env,
        )
        if completed.returncode != 0:
            output.unlink(missing_ok=True)
            raise VideoWorkerError("FFmpeg assembly failed: " + completed.stderr[-400:])
        checked = self.check(job)
        if not checked["ok"]:
            output.unlink(missing_ok=True)
            raise VideoWorkerError("video checker rejected artifact: " + ",".join(checked["reasons"]))
        return {
            "artifact": str(output),
            "artifact_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "checker": checked,
            "source_provenance": provenance,
            "outgoing_spend_usd": "0",
            "social_publish": False,
        }

    def check(self, job: VideoJob) -> dict[str, Any]:
        if job.staging_root is None:
            raise VideoWorkerError("video checker requires an explicit staging root")
        staging_root = Path(job.staging_root).resolve()
        output = _regular_local(job.output_video)
        completed = subprocess.run(
            [self.ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format", "-of", "json", str(output)],
            text=True,
            capture_output=True,
            timeout=30,
            cwd=staging_root,
            env={"PATH": "/usr/bin:/bin", "HOME": str(staging_root), "TMPDIR": str(staging_root)},
        )
        if completed.returncode != 0:
            return {"ok": False, "reasons": ["CORRUPT_CONTAINER"]}
        payload = json.loads(completed.stdout)
        streams = payload.get("streams") or []
        video = next((row for row in streams if row.get("codec_type") == "video"), None)
        audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
        reasons: list[str] = []
        duration = float((payload.get("format") or {}).get("duration") or 0)
        if not video:
            reasons.append("VIDEO_STREAM_MISSING")
        else:
            if int(video.get("width") or 0) != job.width or int(video.get("height") or 0) != job.height:
                reasons.append("RESOLUTION_MISMATCH")
            if str(video.get("codec_name") or "") != "h264":
                reasons.append("VIDEO_CODEC_MISMATCH")
        if job.require_audio and not audio:
            reasons.append("AUDIO_STREAM_MISSING")
        if abs(duration - job.required_duration_seconds) > job.duration_tolerance_seconds:
            reasons.append("DURATION_OUT_OF_BOUNDS")
        if job.require_subtitles and not any(row.get("codec_type") == "subtitle" for row in streams):
            reasons.append("SUBTITLE_STREAM_MISSING")
        if output.stat().st_size < 1024:
            reasons.append("ARTIFACT_TOO_SMALL")
        return {
            "ok": not reasons,
            "reasons": reasons,
            "duration_seconds": duration,
            "width": int((video or {}).get("width") or 0),
            "height": int((video or {}).get("height") or 0),
            "video_codec": (video or {}).get("codec_name"),
            "audio_present": audio is not None,
            "subtitle_present": any(row.get("codec_type") == "subtitle" for row in streams),
            "size_bytes": output.stat().st_size,
        }
