#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from atm_core.video_worker import VideoJob, VideoShortFormWorker


ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "order015-video-benchmark.json"


def main() -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        raise SystemExit("FFMPEG_FFPROBE_REQUIRED")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subtitle = root / "fixture.srt"
        subtitle.write_text("1\n00:00:00,000 --> 00:00:01,800\nATM ZERO SPEND FIXTURE\n", encoding="utf-8")
        source = root / "source.mp4"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-i", str(subtitle), "-shortest", "-c:v", "libx264", "-c:a", "aac", "-c:s", "mov_text", str(source),
            ],
            check=True,
            timeout=120,
        )
        provenance = root / "provenance.json"
        provenance.write_text(json.dumps({"rights": "OWNER_PROVIDED", "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}), encoding="utf-8")
        output = root / "assembled.mp4"
        worker = VideoShortFormWorker()
        result = worker.assemble(VideoJob(source, output, provenance, 2.0, width=360, height=640, staging_root=root))
        receipt = {
            "schema": "ATM_VIDEO_SHORT_FORM_BENCHMARK_V1",
            "upstream_pattern": "MoneyPrinterTurbo v1.3.4 MIT",
            "full_upstream_installed": False,
            "minimal_adapter": "FFMPEG_LOCAL_ASSEMBLER_V1",
            "checker": result["checker"],
            "artifact_sha256": result["artifact_sha256"],
            "source_rights": result["source_provenance"]["rights"],
            "paid_api_fallback": False,
            "social_publish": False,
            "outgoing_spend_usd": "0",
            "winner": "MINIMAL_LOCAL_ADAPTER",
        }
    RECEIPT.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
