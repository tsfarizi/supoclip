"""
Falsification tests for the inter-clip transition feature.

Covered contracts:
  1. transition_spec grammar (pure unit): normalize_transition_spec,
     transition_kind, xfade_name, file_stem, resolve_transition_file,
     clamp_fade, builtin_transition_names.
  2. transition_engine render (ffmpeg integration, skipped when ffmpeg is
     unavailable): merge_clips_with_transition (xfade / none / invalid spec),
     apply_transitions_between_clips (file overlay + fallbacks),
     overlay_transition_mp4 (valid / missing paths).
  3. media API handlers (direct async calls, no DB required): GET /transitions
     listing and GET /transitions/{name}/file traversal rejection.
  4. tasks merge route body contract (DB-gated via the repo conftest; skips
     when DATABASE_URL is not configured).

Fixture assumptions (documented):
  - Bundled transition MP4s exist under backend/transitions/:
    wipe_left.mp4, circle_open.mp4, zoom_fade.mp4 (each ~1.0s).
  - A source MP4 exists under backend/data/ (Uf3hTYt0kGk.mp4 preferred,
    dQw4w9WgXcQ.mp4 fallback); if neither exists the clip fixture generates
    640x360 testsrc2 clips via ffmpeg lavfi.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import FileResponse

from src.config import DEFAULT_FFMPEG_BIN_DIR
from src.transition_spec import (
    DEFAULT_FADE_SECONDS,
    MAX_FADE_SECONDS,
    XFADE_ALLOWLIST,
    builtin_transition_names,
    clamp_fade,
    file_stem,
    normalize_transition_spec,
    resolve_transition_file,
    transition_kind,
    transitions_dir,
    xfade_name,
)
from src.transition_engine import (
    apply_transitions_between_clips,
    merge_clips_with_transition,
    overlay_transition_mp4,
)
from src.video_utils import DEFAULT_COMPOSITION_FADE_SECONDS
from src.video_utils import ffprobe_duration

from src.api.routes.media import (
    get_available_transitions,
    get_transition_file,
)

_FFMPEG_BIN_DIR = Path(os.getenv("FFMPEG_BIN_DIR", DEFAULT_FFMPEG_BIN_DIR))
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_TRANSITIONS_DIR = transitions_dir()

_FFMPEG_REASON = "ffmpeg/ffprobe not found on PATH"


def _ensure_ffmpeg_on_path() -> None:
    """Idempotently put the bundled ffmpeg bin dir on PATH for child processes.

    subprocess inherits os.environ at call time, so prepending once at import
    time makes ffmpeg/ffprobe resolvable for the whole test session regardless
    of the parent shell's PATH.
    """
    if not _FFMPEG_BIN_DIR.is_dir():
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep)
    if str(_FFMPEG_BIN_DIR) not in parts:
        os.environ["PATH"] = str(_FFMPEG_BIN_DIR) + os.pathsep + current


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run_tool(command: list[str], timeout: int = 600) -> None:
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr[-3000:]}"
        )


def _find_source_video() -> Path | None:
    for name in ("Uf3hTYt0kGk.mp4", "dQw4w9WgXcQ.mp4"):
        candidate = _DATA_DIR / name
        if candidate.is_file():
            return candidate
    return None


def _discard_engine_output(path: Path) -> None:
    """Remove an engine output and its throwaway temp dir if we own it."""
    parent = path.parent
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    if parent.name.startswith("supoclip_"):
        shutil.rmtree(parent, ignore_errors=True)


_ensure_ffmpeg_on_path()


# ---------------------------------------------------------------------------
# 1. Spec parsing (pure unit, no ffmpeg needed)
# ---------------------------------------------------------------------------


class TestNormalizeTransitionSpec:
    def test_valid_xfade_is_normalized(self):
        assert normalize_transition_spec("xfade:dissolve") == "xfade:dissolve"

    def test_xfade_unknown_name_falls_back_to_none(self):
        assert normalize_transition_spec("xfade:notreal") == "none"

    def test_xfade_missing_name_falls_back_to_none(self):
        assert normalize_transition_spec("xfade:") == "none"

    def test_valid_file_spec_is_normalized(self):
        assert normalize_transition_spec("file:wipe_left") == "file:wipe_left"

    def test_file_traversal_is_rejected(self):
        assert normalize_transition_spec("file:../evil") == "none"
        assert normalize_transition_spec("file:..") == "none"
        assert normalize_transition_spec("file:..%2f..%2fetc") == "none"

    def test_file_stem_with_extension_is_rejected(self):
        assert normalize_transition_spec("file:wipe_left.mp4") == "none"

    def test_empty_and_none_fall_back_to_none(self):
        assert normalize_transition_spec("") == "none"
        assert normalize_transition_spec(None) == "none"
        assert normalize_transition_spec("   ") == "none"

    def test_explicit_none_keyword_is_kept(self):
        assert normalize_transition_spec("none") == "none"
        assert normalize_transition_spec("NONE") == "none"

    def test_spec_is_case_insensitive(self):
        assert normalize_transition_spec("XFADE:DISSOLVE") == "xfade:dissolve"
        assert normalize_transition_spec("File:Wipe_Left") == "file:wipe_left"

    def test_spec_is_whitespace_trimmed(self):
        assert normalize_transition_spec("  xfade:dissolve  ") == "xfade:dissolve"

    def test_missing_prefix_colon_falls_back_to_none(self):
        assert normalize_transition_spec("dissolve") == "none"
        assert normalize_transition_spec("wipe_left") == "none"

    def test_unknown_prefix_falls_back_to_none(self):
        assert normalize_transition_spec("cut:dissolve") == "none"

    def test_file_prefix_with_empty_tail_falls_back_to_none(self):
        assert normalize_transition_spec("file:") == "none"

    def test_non_string_input_is_coerced_then_rejected(self):
        assert normalize_transition_spec(123) == "none"
        assert normalize_transition_spec(None) == "none"

    def test_xfade_allowlist_members_survive_round_trip(self):
        for name in XFADE_ALLOWLIST:
            assert normalize_transition_spec(f"xfade:{name}") == f"xfade:{name}"


class TestTransitionKind:
    def test_kind_none(self):
        assert transition_kind("none") == "none"
        assert transition_kind("") == "none"
        assert transition_kind("xfade:notreal") == "none"

    def test_kind_xfade(self):
        assert transition_kind("xfade:dissolve") == "xfade"

    def test_kind_file(self):
        assert transition_kind("file:wipe_left") == "file"


class TestXfadeName:
    def test_valid_xfade_returns_name(self):
        assert xfade_name("xfade:dissolve") == "dissolve"

    def test_non_xfade_returns_none(self):
        assert xfade_name("file:wipe_left") is None
        assert xfade_name("none") is None
        assert xfade_name("") is None

    def test_unknown_xfade_returns_none(self):
        assert xfade_name("xfade:notreal") is None


class TestFileStem:
    def test_valid_file_returns_stem(self):
        assert file_stem("file:wipe_left") == "wipe_left"

    def test_non_file_returns_none(self):
        assert file_stem("xfade:dissolve") is None
        assert file_stem("none") is None

    def test_traversal_stem_returns_none(self):
        assert file_stem("file:../evil") is None


class TestBuiltinTransitionNames:
    def test_returns_the_full_allowlist(self):
        names = builtin_transition_names()
        assert names == XFADE_ALLOWLIST
        assert len(names) == 12

    def test_returns_a_copy(self):
        names = builtin_transition_names()
        names.append("injected")
        assert "injected" not in XFADE_ALLOWLIST


class TestClampFade:
    def test_normal_value_is_kept(self):
        assert clamp_fade(0.3, 3.0) == pytest.approx(0.3)

    def test_over_max_is_clamped_to_max(self):
        assert clamp_fade(2.0, 3.0) == pytest.approx(MAX_FADE_SECONDS)

    def test_pair_too_short_for_min_fade_returns_zero(self):
        assert clamp_fade(0.3, 0.05) == 0.0
        assert clamp_fade(0.3, 0.11) == 0.0

    def test_half_pair_caps_the_fade(self):
        # min_pair=0.2 -> upper bound = 0.1
        assert clamp_fade(0.3, 0.2) == pytest.approx(0.1)

    def test_negative_or_zero_returns_zero(self):
        assert clamp_fade(-1.0, 3.0) == 0.0
        assert clamp_fade(0.0, 3.0) == 0.0

    def test_none_inputs_return_zero(self):
        assert clamp_fade(None, 3.0) == 0.0
        assert clamp_fade(0.3, None) == 0.0
        assert clamp_fade(0.3, 0.0) == 0.0

    def test_below_min_requested_fade_is_raised_to_min(self):
        assert clamp_fade(0.02, 3.0) == pytest.approx(0.06)
        assert clamp_fade(0.05, 3.0) == pytest.approx(0.06)
        assert clamp_fade(0.06, 3.0) == pytest.approx(0.06)

    def test_pair_exactly_twice_min_fade_survives(self):
        # min_pair=0.12 -> upper = 0.06, which is not below the min, so the
        # fade is allowed at exactly the minimum.
        assert clamp_fade(0.3, 0.12) == pytest.approx(0.06)


class TestResolveTransitionFile:
    def test_resolves_bundled_transition(self):
        path = resolve_transition_file("file:wipe_left")
        assert path is not None
        assert path == _TRANSITIONS_DIR / "wipe_left.mp4"

    def test_missing_file_returns_none(self):
        assert resolve_transition_file("file:does_not_exist_xyz") is None

    def test_non_file_spec_returns_none(self):
        assert resolve_transition_file("xfade:dissolve") is None
        assert resolve_transition_file("none") is None

    def test_respects_custom_base_dir(self, tmp_path):
        (tmp_path / "custom.mp4").write_bytes(b"not-a-real-mp4")
        resolved = resolve_transition_file("file:custom", base_dir=tmp_path)
        assert resolved == tmp_path / "custom.mp4"
        assert resolve_transition_file("file:custom", base_dir=tmp_path / "nope") is None

    def test_transitions_dir_points_at_backend_transitions(self):
        assert _TRANSITIONS_DIR.is_dir()
        assert _TRANSITIONS_DIR.name == "transitions"


# ---------------------------------------------------------------------------
# Engine validation (no ffmpeg needed; raised before any render)
# ---------------------------------------------------------------------------


class TestTransitionEngineValidation:
    def test_merge_requires_at_least_two_clips(self, tmp_path):
        with pytest.raises(ValueError):
            merge_clips_with_transition([tmp_path / "a.mp4"], "none")

    def test_hook_path_is_first_input_and_none_still_composes_with_fade(
        self, tmp_path, monkeypatch
    ):
        hook = tmp_path / "hook.mp4"
        main = tmp_path / "main.mp4"
        hook.write_bytes(b"hook")
        main.write_bytes(b"main")
        captured = {}

        monkeypatch.setattr("src.transition_engine.ffprobe_duration", lambda _path: 4.0)
        monkeypatch.setattr(
            "src.transition_engine._render_chained_xfade",
            lambda paths, durations, name, fade: captured.update(
                paths=[Path(path) for path in paths],
                durations=durations,
                name=name,
                fade=fade,
            ) or tmp_path / "merged.mp4",
        )

        result = merge_clips_with_transition([main], "none", hook_path=hook)

        assert result == tmp_path / "merged.mp4"
        assert captured["paths"] == [hook, main]
        assert captured["name"] == "fade"
        assert captured["fade"] == DEFAULT_COMPOSITION_FADE_SECONDS

    def test_merge_rejects_missing_clip_files(self, tmp_path):
        with pytest.raises(ValueError):
            merge_clips_with_transition(
                [tmp_path / "a.mp4", tmp_path / "b.mp4"], "none"
            )

    def test_apply_transitions_requires_at_least_two_clips(self, tmp_path):
        with pytest.raises(ValueError):
            apply_transitions_between_clips([tmp_path / "a.mp4"], "none", tmp_path)


# ---------------------------------------------------------------------------
# 2. Engine render (integration, needs ffmpeg/ffprobe)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def sample_clips(tmp_path_factory):
    """Three ~2.5s 640x360 H.264 yuv420p clips (video only), identical encodes."""
    work = tmp_path_factory.mktemp("transition_clips")
    source = _find_source_video()
    clips: list[Path] = []

    if source is not None:
        for index, start in enumerate((0.0, 5.0, 10.0)):
            out = work / f"clip_{index}.mp4"
            _run_tool(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}", "-t", "2.5",
                    "-i", str(source),
                    "-vf", "scale=640:360:flags=lanczos,fps=30,format=yuv420p",
                    "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-movflags", "+faststart",
                    str(out),
                ]
            )
            clips.append(out)
    else:
        for index in range(3):
            out = work / f"clip_{index}.mp4"
            _run_tool(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=30:duration=2.5",
                    "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-movflags", "+faststart",
                    str(out),
                ]
            )
            clips.append(out)

    for path in clips:
        assert path.is_file(), f"failed to create test clip {path}"
        assert ffprobe_duration(path) >= 2.0, f"test clip too short: {path}"
    return clips


class TestMergeClipsWithTransition:
    pytestmark = pytest.mark.skipif(
        not _ffmpeg_available(), reason=_FFMPEG_REASON
    )

    def test_xfade_dissolve_duration_is_sum_minus_fade(self, sample_clips):
        a, b = sample_clips[0], sample_clips[1]
        d_a, d_b = ffprobe_duration(a), ffprobe_duration(b)
        out = merge_clips_with_transition([a, b], "xfade:dissolve")
        try:
            assert out.is_file()
            fade = clamp_fade(DEFAULT_FADE_SECONDS, min(d_a, d_b))
            actual = ffprobe_duration(out)
            assert (
                abs(actual - (d_a + d_b - fade)) <= 0.3
            ), f"expected ~{d_a + d_b - fade:.2f}s, got {actual:.2f}s"
        finally:
            _discard_engine_output(out)

    def test_none_spec_is_hard_concat(self, sample_clips):
        a, b = sample_clips[0], sample_clips[1]
        d_a, d_b = ffprobe_duration(a), ffprobe_duration(b)
        out = merge_clips_with_transition([a, b], "none")
        try:
            assert out.is_file()
            actual = ffprobe_duration(out)
            assert (
                abs(actual - (d_a + d_b)) <= 0.3
            ), f"expected ~{d_a + d_b:.2f}s, got {actual:.2f}s"
        finally:
            _discard_engine_output(out)

    def test_invalid_spec_falls_back_to_hard_concat(self, sample_clips):
        a, b = sample_clips[0], sample_clips[1]
        d_a, d_b = ffprobe_duration(a), ffprobe_duration(b)
        for spec in ("xfade:notreal", "file:../evil", "bogus"):
            out = merge_clips_with_transition([a, b], spec)
            try:
                assert out.is_file(), f"spec {spec!r} did not produce output"
                actual = ffprobe_duration(out)
                assert (
                    abs(actual - (d_a + d_b)) <= 0.3
                ), f"spec {spec!r}: expected concat ~{d_a + d_b:.2f}s, got {actual:.2f}s"
            finally:
                _discard_engine_output(out)


class TestApplyTransitionsBetweenClips:
    pytestmark = pytest.mark.skipif(
        not _ffmpeg_available(), reason=_FFMPEG_REASON
    )

    def test_file_spec_overlays_and_shortens_by_window(self, sample_clips, tmp_path):
        transition = _TRANSITIONS_DIR / "wipe_left.mp4"
        a, b = sample_clips[0], sample_clips[1]
        d_a, d_b = ffprobe_duration(a), ffprobe_duration(b)
        window = min(ffprobe_duration(transition), d_a, d_b)

        out = apply_transitions_between_clips([a, b], "file:wipe_left", tmp_path)
        assert out.is_file()
        actual = ffprobe_duration(out)
        assert (
            abs(actual - (d_a + d_b - window)) <= 0.3
        ), f"expected ~{d_a + d_b - window:.2f}s, got {actual:.2f}s"

    def test_missing_file_spec_falls_back_to_xfade(self, sample_clips, tmp_path):
        a, b = sample_clips[0], sample_clips[1]
        d_a, d_b = ffprobe_duration(a), ffprobe_duration(b)
        fade = clamp_fade(DEFAULT_FADE_SECONDS, min(d_a, d_b))

        out = apply_transitions_between_clips([a, b], "file:missing", tmp_path)
        assert out.is_file(), "file:missing must produce output via fallback, not raise"
        actual = ffprobe_duration(out)
        assert (
            abs(actual - (d_a + d_b - fade)) <= 0.3
        ), f"fallback xfade: expected ~{d_a + d_b - fade:.2f}s, got {actual:.2f}s"


class TestOverlayTransitionMp4:
    pytestmark = pytest.mark.skipif(
        not _ffmpeg_available(), reason=_FFMPEG_REASON
    )

    def test_valid_transition_file_returns_true(self, sample_clips, tmp_path):
        a, b = sample_clips[0], sample_clips[1]
        out = tmp_path / "overlay_valid.mp4"
        ok = overlay_transition_mp4(a, b, _TRANSITIONS_DIR / "wipe_left.mp4", out)
        assert ok is True
        assert out.is_file()

    def test_missing_transition_path_returns_false(self, sample_clips, tmp_path):
        a, b = sample_clips[0], sample_clips[1]
        out = tmp_path / "overlay_missing.mp4"
        assert overlay_transition_mp4(a, b, tmp_path / "nope.mp4", out) is False
        assert not out.exists()

    def test_missing_clip_path_returns_false(self, sample_clips, tmp_path):
        b = sample_clips[1]
        out = tmp_path / "overlay_missing_clip.mp4"
        assert (
            overlay_transition_mp4(tmp_path / "nope.mp4", b, _TRANSITIONS_DIR / "wipe_left.mp4", out)
            is False
        )
        assert not out.exists()


# ---------------------------------------------------------------------------
# 3. Media API handlers (direct async calls; no DB dependency)
# ---------------------------------------------------------------------------


class TestMediaTransitionsApi:
    async def test_get_transitions_lists_builtin_and_bundled_files(self):
        payload = await get_available_transitions()
        transitions = payload["transitions"]

        builtin = [t for t in transitions if t["kind"] == "builtin"]
        files = [t for t in transitions if t["kind"] == "file"]

        assert len(builtin) == 12
        assert {t["name"] for t in builtin} == set(XFADE_ALLOWLIST)
        assert len(files) == 3
        assert {t["name"] for t in files} == {
            "wipe_left",
            "circle_open",
            "zoom_fade",
        }
        for t in transitions:
            assert t["display_name"]
            assert t["kind"] in {"builtin", "file"}

    async def test_get_transition_file_valid_name_returns_file_response(self):
        response = await get_transition_file("wipe_left")
        assert isinstance(response, FileResponse)
        assert Path(response.path).name == "wipe_left.mp4"
        assert Path(response.path).is_file()

    async def test_get_transition_file_valid_name_hermetic(self, tmp_path, monkeypatch):
        (tmp_path / "sample.mp4").write_bytes(b"not-a-real-mp4")
        from src.api.routes import media as media_module

        monkeypatch.setattr(media_module, "_transitions_dir", lambda: tmp_path)
        response = await get_transition_file("sample")
        assert isinstance(response, FileResponse)
        assert Path(response.path) == tmp_path / "sample.mp4"

    async def test_get_transition_file_rejects_unsafe_names(self, tmp_path, monkeypatch):
        from src.api.routes import media as media_module

        monkeypatch.setattr(media_module, "_transitions_dir", lambda: tmp_path)
        (tmp_path / "wipe_left.mp4").write_bytes(b"x")
        for unsafe in (
            "../evil",
            "..",
            "../wipe_left",
            "..%2fwipe_left",
            "a/b",
            "wipe_left.mp4",
            "wipe left",
            "WIPE_LEFT",
            "",
            ".",
        ):
            with pytest.raises(HTTPException) as excinfo:
                await get_transition_file(unsafe)
            assert excinfo.value.status_code == 404, f"name {unsafe!r} not rejected"

    async def test_get_transition_file_missing_safe_name_is_404(self, tmp_path, monkeypatch):
        from src.api.routes import media as media_module

        monkeypatch.setattr(media_module, "_transitions_dir", lambda: tmp_path)
        with pytest.raises(HTTPException) as excinfo:
            await get_transition_file("definitely_missing")
        assert excinfo.value.status_code == 404


# ---------------------------------------------------------------------------
# 4. POST /{task_id}/clips/merge route body contract (DB-gated)
# ---------------------------------------------------------------------------


class TestTasksMergeRouteContract:
    """Body schema contract only; no merge actually mutates data.

    The merge itself is never executed with renderable clips here: we POST a
    payload whose clip count fails pre-validation, proving the route parses and
    forwards the `transition` field without a schema error (422) or a crash.
    """

    async def test_merge_route_accepts_transition_body_and_validates_clips(
        self, client, db_session, auth_headers
    ):
        from sqlalchemy import text

        from tests.fixtures.factories import create_clip, create_source, create_task, create_user

        user = await create_user(db_session, user_id="user-1", email="owner@example.com")
        source = await create_source(db_session, title="Merge contract source")
        task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
        clip = await create_clip(db_session, task_id=task["id"], text_value="clip one")

        try:
            response = await client.post(
                f"/tasks/{task['id']}/clips/merge",
                headers=auth_headers,
                json={"clip_ids": [clip["id"]], "transition": "xfade:dissolve"},
            )
            assert response.status_code == 400
            assert "At least two clips" in response.json()["detail"]
        finally:
            # Remove every row this test created so a live dev DB stays clean.
            await client.delete(f"/tasks/{task['id']}", headers=auth_headers)
            await db_session.execute(
                text("DELETE FROM sources WHERE id = :sid"), {"sid": source["id"]}
            )
            await db_session.execute(
                text("DELETE FROM users WHERE id = :uid"), {"uid": user["id"]}
            )
            await db_session.commit()
