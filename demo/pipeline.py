"""Orchestrates the full demo video pipeline: record → render → composite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from demo.schema import Screenplay


def run_pipeline(
    screenplay: Screenplay,
    output_path: Path,
    skin_override: Optional[str] = None,
    typing_speed_override: Optional[float] = None,
    dry_run: bool = False,
) -> None:
    """Run the full recording + rendering pipeline.

    Steps:
    1. Record terminal session with asciinema (running player in --play mode)
    2. Render .cast → GIF with agg at high resolution
    3. Composite: read GIF frames, apply camera crop in Python, encode to MP4
    """
    from demo.compositor import composite_frames
    from demo.recorder import render_agg, record_asciinema

    out = screenplay.output
    work_dir = output_path.parent
    stem = output_path.stem

    cast_path = work_dir / f"{stem}.cast"
    gif_path = work_dir / f"{stem}.gif"
    timing_path = work_dir / f"{stem}_timing.json"

    # Build the player command
    player_cmd = [
        sys.executable, "-m", "demo",
        "--play",  # Terminal playback mode
        "--timing", str(timing_path),
    ]
    if skin_override:
        player_cmd.extend(["--skin", skin_override])
    elif screenplay.skin != "default":
        player_cmd.extend(["--skin", screenplay.skin])
    if typing_speed_override:
        player_cmd.extend(["--typing-speed", str(typing_speed_override)])

    # We need the screenplay path — it's set by __main__ on the screenplay object
    script_path = getattr(screenplay, "_source_path", None)
    if script_path:
        player_cmd.append(str(script_path))
    else:
        raise RuntimeError("Screenplay has no _source_path — cannot record")

    # Calculate terminal size for recording
    cols = 200
    rows = 50

    if dry_run:
        _print_dry_run(
            player_cmd, cast_path, gif_path, timing_path,
            out, screenplay, output_path,
        )
        return

    # Step 1: Record with asciinema
    print(f"[1/3] Recording terminal session → {cast_path}")
    record_asciinema(
        command=player_cmd,
        cast_path=cast_path,
        cols=cols,
        rows=rows,
    )

    # Step 2: Render to GIF with agg
    print(f"[2/3] Rendering → {gif_path}")
    render_agg(
        cast_path=cast_path,
        gif_path=gif_path,
        font_size=out.font_size,
        font_family=out.font_family,
        fps=out.fps,
        theme=out.theme,
    )

    # Step 3: Resolve camera keyframes and composite
    keyframes = _resolve_camera_keyframes(screenplay, timing_path)

    n_camera = sum(1 for kf in keyframes if kf.zoom != 1.0 or kf.x != 0.5 or kf.y != 0.5)
    print(f"[3/3] Compositing → {output_path} ({n_camera} camera keyframe(s))")

    composite_frames(
        gif_path=gif_path,
        output_path=output_path,
        keyframes=keyframes,
        output_w=out.final_width,
        output_h=out.final_height,
        fps=out.fps,
        bg_image=out.bg_image,
        bg_opacity=out.bg_opacity,
        bg_color=out.bg_color,
    )

    print(f"Done! Output: {output_path}")


def _resolve_camera_keyframes(screenplay, timing_path):
    """Load timing manifest and resolve camera keyframes."""
    from demo.camera import CameraKeyframe, resolve_keyframes
    from demo.scenes import TimingManifest, SceneTiming

    if not timing_path.exists():
        # No timing data — return a single default keyframe (no zoom)
        return [CameraKeyframe(t=0.0, zoom=1.0, x=0.5, y=0.5, duration=0.0, ease="linear")]

    import json
    timing_data = json.loads(timing_path.read_text())
    manifest = TimingManifest(total_duration=timing_data.get("total_duration", 0))
    for sd in timing_data.get("scenes", []):
        st = SceneTiming(
            index=sd["index"],
            scene_type=sd["type"],
            action=sd.get("action"),
            start_t=sd["start_t"],
            end_t=sd["end_t"],
            markers=sd.get("markers", {}),
        )
        manifest.scenes.append(st)

    return resolve_keyframes(screenplay, manifest)


def _print_dry_run(
    player_cmd, cast_path, gif_path, timing_path,
    out, screenplay, output_path,
):
    """Print the commands that would be run without executing them."""
    print("=== DRY RUN ===\n")

    print("Step 1: Record with asciinema")
    print("  asciinema rec --overwrite --cols=200 --rows=50 \\")
    print(f"    --command '{' '.join(player_cmd)}' \\")
    print(f"    {cast_path}\n")

    print("Step 2: Render with agg")
    print(f"  agg --font-size={out.font_size} --font-family='{out.font_family}' \\")
    print(f"    --fps-cap={out.fps} {cast_path} {gif_path}\n")

    scenes = screenplay.parsed_scenes()
    camera_count = sum(
        1 for s in scenes
        if hasattr(s, "camera") and s.camera
        or hasattr(s, "camera_response") and s.camera_response
        or (hasattr(s, "action") and s.action == "camera")
    )

    print("Step 3: Composite (Python frame-by-frame crop + ffmpeg encode)")
    print(f"  {camera_count} camera directive(s) in screenplay")
    print("  Read GIF frames → crop per camera keyframe → pipe to ffmpeg")
    print(f"  Output: {out.final_width}x{out.final_height} @ {out.fps}fps → {output_path}")
