"""Falsification tests for the SFX boundary contracts.

All provider and media boundaries are replaced with deterministic doubles.  No
test in this module contacts Freesound or invokes a real encoder.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src import freesound, sound_effect_cache, video_utils
from src import config as config_module
from src.ai import SoundEffectOpportunity, TranscriptSegment, _validate_sound_effect_opportunities
from src.config import Config, set_config_override
from src.observability import redact_secrets
from src.services.task_service import TaskService
from src.services.video_service import VideoService


TOKEN = "test-token-that-must-not-appear-in-errors"


def _sound(**overrides):
    data = {
        "id": 7, "name": "Door", "username": "creator", "license": "CC0",
        "previews": {"preview-hq-mp3": "https://cdn.test/source.ogg"},
        "duration": 1.0, "type": "wav", "filesize": 10, "url": "https://freesound.org/s/7",
        "gen_ai_preference": "allowed", "score": 0.9,
    }
    data.update(overrides)
    return freesound.FreesoundSound.model_validate(data)


class _FakeClient:
    responses = []
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def stream(self, *_args, **_kwargs):
        response = self.responses.pop(0)

        class _ResponseStream:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *_exit_args):
                return False

            async def aiter_bytes(self_inner, _chunk_size):
                yield response.content

        return _ResponseStream()


class _StreamingResponse:
    def __init__(self, status_code=200, *, content_type="audio/mpeg", blocks=()):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._blocks = tuple(blocks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_bytes(self, _chunk_size):
        for block in self._blocks:
            yield block


class _StreamingClient:
    response = None
    calls = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        self.calls.append((_args, _kwargs))
        return self.response


def _response(status=200, *, json_data=None, headers=None, content=b""):
    body = json.dumps(json_data).encode() if json_data is not None else content
    return httpx.Response(status, headers=headers, content=body)


@pytest.mark.asyncio
async def test_freesound_search_sends_token_in_authorization_header(monkeypatch):
    config = Config()
    config.freesound_api_key = TOKEN
    set_config_override(config)
    _FakeClient.calls = []
    _FakeClient.responses = [_response(json_data={"results": []})]
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)
    try:
        await freesound.search_freesound_sounds("  door slam  ", limit=3)
    finally:
        set_config_override(None)
    _, kwargs = _FakeClient.calls[0]
    assert kwargs["params"]["query"] == "door slam"
    assert kwargs["params"]["page_size"] == 3
    assert kwargs["params"]["fields"] == freesound.FREESOUND_FIELDS
    assert kwargs["headers"]["Authorization"] == f"Token {TOKEN}"
    assert "token" not in kwargs["params"]


@pytest.mark.asyncio
async def test_freesound_permanent_error_does_not_retry_or_leak_token(monkeypatch, caplog):
    config = Config(); config.freesound_api_key = TOKEN; set_config_override(config)
    _FakeClient.calls = []; _FakeClient.responses = [_response(401)]
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)
    try:
        result = await freesound.search_freesound_sounds("bell")
    finally:
        set_config_override(None)
    assert result == []
    assert len(_FakeClient.calls) == 1
    assert TOKEN not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(freesound.RETRYABLE_STATUS_CODES))
async def test_freesound_retries_each_retryable_status(monkeypatch, status):
    config = Config(); config.freesound_api_key = TOKEN; set_config_override(config)
    _FakeClient.calls = []
    _FakeClient.responses = [_response(status), _response(200, json_data={"results": []})]
    sleeps = []
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)
    async def fake_sleep(delay):
        sleeps.append(delay)
    monkeypatch.setattr(freesound.asyncio, "sleep", fake_sleep)
    try:
        assert await freesound.search_freesound_sounds("bell") == []
    finally:
        set_config_override(None)
    assert len(_FakeClient.calls) == 2
    assert sleeps


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [501, 505])
async def test_freesound_does_not_retry_non_retryable_501_or_505(monkeypatch, status):
    """Provider contract: 501/505 are terminal responses, not retry signals."""
    config = Config(); config.freesound_api_key = TOKEN; set_config_override(config)
    _FakeClient.calls = []; _FakeClient.responses = [_response(status)]
    sleeps = []
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(freesound.asyncio, "sleep", fake_sleep)
    try:
        assert await freesound.search_freesound_sounds("bell") == []
    finally:
        set_config_override(None)

    assert len(_FakeClient.calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_freesound_retry_after_overrides_backoff(monkeypatch):
    config = Config(); config.freesound_api_key = TOKEN; set_config_override(config)
    _FakeClient.responses = [_response(429, headers={"Retry-After": "7"}), _response(200, json_data={"results": []})]
    delays = []
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)
    async def fake_sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(freesound.asyncio, "sleep", fake_sleep)
    try:
        await freesound.search_freesound_sounds("bell")
    finally:
        set_config_override(None)
    assert delays == [7.0]


def test_freesound_license_filter_rejects_non_open_and_accepts_attribution():
    assert freesound._translate_sound({**_sound().model_dump(), "license": "All Rights Reserved"}) is None
    assert freesound._translate_sound({**_sound().model_dump(), "license": "https://creativecommons.org/licenses/by/3.0/"}) is not None
    assert freesound._translate_sound({**_sound().model_dump(), "previews": {}}) is None


@pytest.mark.asyncio
async def test_freesound_download_normalizes_any_source_extension_to_mp3(monkeypatch, tmp_path):
    _FakeClient.responses = [_response(200, headers={"Content-Type": "audio/mpeg", "Content-Length": "3"}, content=b"ID3")]
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _FakeClient)
    result = await freesound.download_freesound_mp3(_sound(), tmp_path / "download.ogg")
    assert result == tmp_path / "download.mp3"
    assert result.read_bytes() == b"ID3"


def _seed_cached(tmp_path, monkeypatch, *, task_id="task", payload=b"ID3-test"):
    monkeypatch.setenv("SOUND_EFFECT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(sound_effect_cache, "_audio_is_readable", lambda _path: True)
    source = tmp_path / "source.bin"; source.write_bytes(payload)
    return sound_effect_cache.cache_sound_effect(_sound(), source, task_id)


def test_sound_effect_cache_roundtrip_preserves_attribution_and_ttl(monkeypatch, tmp_path):
    entry = _seed_cached(tmp_path, monkeypatch)
    loaded = sound_effect_cache.get_cached_sound_effect("freesound", 7)
    assert loaded is not None
    assert loaded.attribution.creator == "creator"
    sidecar = loaded.path.with_suffix(".json")
    data = json.loads(sidecar.read_text())
    data["fetched_at"] -= sound_effect_cache.TTL_SECONDS + 1
    sidecar.write_text(json.dumps(data))
    assert sound_effect_cache.get_cached_sound_effect("freesound", 7) is None
    assert entry.attribution.source_url == "https://freesound.org/s/7"


def test_sound_effect_cache_size_cleanup_evicts_oldest_referenced_asset(monkeypatch, tmp_path):
    first = _seed_cached(tmp_path, monkeypatch, task_id="task-a", payload=b"ID3-first")
    second = _seed_cached(tmp_path, monkeypatch, task_id="task-b", payload=b"ID3-second")
    monkeypatch.setattr(sound_effect_cache, "MAX_CACHE_BYTES", first.size_bytes + second.size_bytes - 1)
    now = 10_000.0
    for entry, access in ((first, 1.0), (second, 2.0)):
        sidecar = entry.path.with_suffix(".json")
        data = json.loads(sidecar.read_text()); data["fetched_at"] = now; data["last_access"] = access
        sidecar.write_text(json.dumps(data))
    assert sound_effect_cache.cleanup_sound_effect_cache(now=now) == 1
    assert not first.path.exists()
    assert second.path.exists()


def test_sound_effect_cache_lookup_returns_handle_and_updates_last_access(monkeypatch, tmp_path):
    entry = _seed_cached(tmp_path, monkeypatch)
    sidecar = entry.path.with_suffix(".json")
    payload = json.loads(sidecar.read_text())
    payload["last_access"] = 1.0
    sidecar.write_text(json.dumps(payload))
    monkeypatch.setattr(sound_effect_cache.time, "time", lambda: 2.0)

    loaded = sound_effect_cache.get_cached_sound_effect("freesound", 7)

    assert loaded is not None
    assert loaded.path == entry.path
    assert json.loads(sidecar.read_text())["last_access"] == 2.0


@pytest.mark.parametrize("count", range(6))
def test_ai_sfx_count_zero_to_five_is_bounded_and_grounded(count):
    opportunity = SoundEffectOpportunity(source_timestamp="00:02", duration=1, query="door", context="door", intensity="subtle", gain_db=0, placement="main")
    result = _validate_sound_effect_opportunities([opportunity], [{"start": 0, "end": 3}], count)
    assert result is None if count == 0 else len(result) == 1


def test_ai_sfx_validation_discards_out_of_transcript_timestamp_and_rejects_negative_count():
    opportunity = SoundEffectOpportunity(source_timestamp="00:09", duration=1, query="door", context="door", intensity="subtle", gain_db=0, placement="main")
    assert _validate_sound_effect_opportunities([opportunity], [{"start": 0, "end": 3}], 5) is None
    with pytest.raises(ValueError, match="greater than or equal to zero"):
        _validate_sound_effect_opportunities([], [], -1)


def test_audio_mixer_applies_position_gain_fades_and_placement(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    commands = []
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", lambda command, timeout: (commands.append(command), output.write_bytes(b"mixed"), SimpleNamespace(returncode=0))[2])
    result = video_utils.mix_clip_audio_with_sfx(clip, [{"asset_path": asset, "start_seconds": 1.25, "end_seconds": 2.75, "volume": 0.5, "fade_in_seconds": 0.2, "fade_out_seconds": 0.3, "placement": "hook"}], output, clip_duration=4)
    assert result.status == "mixed" and result.applied_sfx == 1
    command = " ".join(commands[0])
    assert "volume=0.500000" in command and "afade=t=in" in command and "afade=t=out" in command and "adelay=1250" in command


def test_audio_mixer_discards_invalid_overlap_and_reports_degraded(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", lambda command, timeout: (output.write_bytes(b"mixed"), SimpleNamespace(returncode=0))[1])
    result = video_utils.mix_clip_audio_with_sfx(clip, [
        {"asset_path": asset, "start_seconds": 0, "end_seconds": 2, "placement": "main"},
        {"asset_path": asset, "start_seconds": 1, "end_seconds": 3, "placement": "main"},
    ], output, clip_duration=4, max_layers=1)
    assert result.status == "degraded" and result.applied_sfx == 1 and result.discarded_sfx == 1


def test_audio_mixer_ffmpeg_failure_is_failed_and_removes_output(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", lambda command, timeout: SimpleNamespace(returncode=1))
    result = video_utils.mix_clip_audio_with_sfx(clip, [{"asset_path": asset, "start_seconds": 0, "end_seconds": 1}], output, clip_duration=2)
    assert result.status == "failed" and result.output_path is None and not output.exists()


def test_audio_mixer_uses_ffmpeg90_compatible_alimiter_without_level(monkeypatch, tmp_path):
    """Regression (alimiter fix): the amix chain must end in the corrected
    ``alimiter=limit=0.95`` (limit is a double in every FFmpeg) and must NOT
    carry the old float ``level=`` gain, which FFmpeg 9.0 rejects with a
    boolean parse error. A fully valid mix must report ``mixed``, never
    ``degraded``."""
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    commands = []
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)

    def _fake_ffmpeg(command, timeout):
        commands.append(list(command))
        output.write_bytes(b"mixed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(video_utils, "run_ffmpeg_command", _fake_ffmpeg)

    result = video_utils.mix_clip_audio_with_sfx(
        clip,
        [
            {"asset_path": asset, "start_seconds": 0.25, "end_seconds": 1.25, "volume": 0.5, "placement": "main"},
            {"asset_path": asset, "start_seconds": 1.5, "end_seconds": 2.5, "volume": 0.25, "placement": "hook"},
        ],
        output,
        clip_duration=3,
    )

    assert result.status == "mixed", result.reason
    assert result.applied_sfx == 2 and result.discarded_sfx == 0
    filter_complex = commands[0][commands[0].index("-filter_complex") + 1]
    amix_chain = filter_complex.rsplit("amix=", 1)[1]
    assert amix_chain == (
        "inputs=3:duration=first:dropout_transition=0:normalize=1,"
        "alimiter=limit=0.95[amixed]"
    ), amix_chain
    assert "level=" not in amix_chain


def test_audio_mixer_alimiter_parse_failure_is_failed_not_degraded(monkeypatch, tmp_path):
    """Regression (alimiter fix): the exact FFmpeg 9.0 failure mode of the old
    float ``level=`` option — an encoder rejection of the filter graph — must
    be ``failed`` with the output removed, never ``degraded`` (which would
    silently ship the un-mixed source as a "success")."""
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(
        video_utils,
        "run_ffmpeg_command",
        lambda command, timeout: SimpleNamespace(
            returncode=1, stderr='Unable to parse option value "0.95" as boolean'
        ),
    )

    result = video_utils.mix_clip_audio_with_sfx(
        clip, [{"asset_path": asset, "start_seconds": 0, "end_seconds": 1}], output, clip_duration=2
    )

    assert result.status == "failed"
    assert result.output_path is None
    assert not output.exists()
    assert result.reason == "ffmpeg audio mix failed"


def test_audio_mixer_no_sfx_returns_unchanged_and_copies_source_without_ffmpeg(monkeypatch, tmp_path):
    """Contract clause: with zero SFX placements the mixer must report
    ``unchanged`` and copy the source through — it must not invoke ffmpeg at
    all (the video is already correct without a re-encode)."""
    clip = tmp_path / "clip.mp4"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip-bytes")
    calls = []
    monkeypatch.setattr(
        video_utils,
        "run_ffmpeg_command",
        lambda command, timeout: (calls.append(list(command)), SimpleNamespace(returncode=0))[1],
    )

    result = video_utils.mix_clip_audio_with_sfx(clip, [], output, clip_duration=2)

    assert result.status == "unchanged"
    assert result.output_path == output
    assert result.applied_sfx == 0 and result.discarded_sfx == 0
    assert calls == []
    assert output.read_bytes() == b"clip-bytes"


def test_audio_mixer_without_source_audio_synthesizes_base_via_anullsrc(monkeypatch, tmp_path):
    """Contract clause: a clip with no audio stream gets a synthesized 48 kHz
    stereo base (``anullsrc``) so the SFX mix still produces a full mix."""
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    commands = []
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: False)

    def _fake_ffmpeg(command, timeout):
        commands.append(list(command))
        output.write_bytes(b"mixed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(video_utils, "run_ffmpeg_command", _fake_ffmpeg)

    result = video_utils.mix_clip_audio_with_sfx(
        clip, [{"asset_path": asset, "start_seconds": 0, "end_seconds": 1}], output, clip_duration=2
    )

    assert result.status == "mixed" and result.applied_sfx == 1
    command = " ".join(commands[0])
    assert "anullsrc=r=48000:cl=stereo:d=2.000" in command
    assert "[1:a]aresample=48000" in command  # synthesized base, not [0:a]
    assert "amix=inputs=2" in command


def _resolve_executable(name):
    """Locate an ffmpeg-family executable via Config().ffmpeg_bin_dir then PATH."""
    from src.config import Config

    bin_dir = Config().ffmpeg_bin_dir
    suffix = ".exe" if os.name == "nt" else ""
    if bin_dir:
        candidate = Path(bin_dir) / f"{name}{suffix}"
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


@pytest.fixture(scope="module")
def ffmpeg_toolchain():
    """Resolve real ffmpeg/ffprobe binaries; SKIP the real-encoder test when absent."""
    ffmpeg_bin = _resolve_executable("ffmpeg")
    ffprobe_bin = _resolve_executable("ffprobe")
    if not ffmpeg_bin or not ffprobe_bin:
        pytest.skip(
            "ffmpeg/ffprobe not found via Config().ffmpeg_bin_dir or PATH "
            f"(ffmpeg={ffmpeg_bin!r}, ffprobe={ffprobe_bin!r}) — real ffmpeg SFX mixer test skipped"
        )
    return {"ffmpeg": ffmpeg_bin, "ffprobe": ffprobe_bin}


class _RealRunner:
    """run_ffmpeg_command replacement that shells to the real ffmpeg/ffprobe."""

    def __init__(self, toolchain):
        self.toolchain = toolchain
        self.calls = []

    def run(self, command, timeout=900):
        replaced = [
            self.toolchain["ffmpeg"] if arg == "ffmpeg"
            else self.toolchain["ffprobe"] if arg == "ffprobe"
            else arg
            for arg in command
        ]
        result = subprocess.run(replaced, capture_output=True, text=True, timeout=timeout)
        self.calls.append((list(command), result))
        return result


def test_audio_mixer_real_ffmpeg_executes_corrected_alimiter_filter(monkeypatch, tmp_path, ffmpeg_toolchain):
    """Regression (alimiter fix): the corrected filter graph must complete a
    REAL ffmpeg pass and report ``mixed``. Reintroducing the old float
    ``level=0.95`` makes FFmpeg 9.0 fail with a boolean parse error and turns
    this test red. ``degraded`` must never be reported for a clean mix."""
    ffmpeg_bin = ffmpeg_toolchain["ffmpeg"]

    def _run(cmd):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, f"fixture generation failed:\n{result.stderr[-1500:]}"
        return result

    clip = tmp_path / "clip.mp4"
    _run([
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
        "-t", "2", str(clip),
    ])
    asset = tmp_path / "effect.wav"
    _run([
        ffmpeg_bin, "-y",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=1",
        "-c:a", "pcm_s16le", "-ar", "48000", str(asset),
    ])
    output = tmp_path / "out.mp4"

    runner = _RealRunner(ffmpeg_toolchain)
    monkeypatch.setattr(video_utils, "run_ffmpeg_command", runner.run)
    result = video_utils.mix_clip_audio_with_sfx(
        clip,
        [{"asset_path": asset, "start_seconds": 0.25, "end_seconds": 1.25, "volume": 0.5, "placement": "main"}],
        output,
        clip_duration=2.0,
    )

    assert result.status == "mixed", f"real ffmpeg mix failed: {result.reason}"
    assert result.applied_sfx == 1 and result.discarded_sfx == 0
    assert output.exists() and output.stat().st_size > 0
    mix_command = next(cmd for cmd, _ in runner.calls if "-filter_complex" in cmd)
    filter_complex = mix_command[mix_command.index("-filter_complex") + 1]
    assert "alimiter=limit=0.95[amixed]" in filter_complex
    amix_chain = filter_complex.rsplit("amix=", 1)[1]
    assert "level=" not in amix_chain
    probe = subprocess.run(
        [ffmpeg_toolchain["ffprobe"], "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, timeout=60,
    )
    assert probe.returncode == 0 and "aac" in probe.stdout, probe.stdout


def test_audio_mixer_rejects_sfx_beyond_stream_duration(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"; asset = tmp_path / "effect.mp3"; output = tmp_path / "out.mp4"
    clip.write_bytes(b"clip"); asset.write_bytes(b"ID3")
    monkeypatch.setattr(video_utils, "ffprobe_has_audio", lambda _path: True)

    result = video_utils.mix_clip_audio_with_sfx(
        clip, [{"asset_path": asset, "start_seconds": 1, "end_seconds": 3}],
        output, clip_duration=2,
    )

    assert result.status == "degraded"
    assert result.applied_sfx == 0
    assert result.discarded_sfx == 1
    assert output.read_bytes() == b"clip"


def test_sfx_source_timestamp_is_placed_on_compacted_hook_main_timeline():
    # Two sufficiently long ranges use the 0.22-second crossfade and therefore
    # occupy 1.78 seconds before the main range begins on the output timeline.
    assert VideoService._source_to_local_timestamp(
        5.5, (0.0, 2.0), [(5.0, 8.0)], "main"
    ) == pytest.approx(2.28)
    assert VideoService._source_to_local_timestamp(
        1.5, (0.0, 2.0), [(5.0, 8.0)], "transition"
    ) == pytest.approx(1.78)


@pytest.mark.asyncio
async def test_sfx_sidecar_marks_provider_failure_as_degraded(monkeypatch, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"clip")

    async def provider_failure(*_args, **_kwargs):
        raise freesound.FreesoundProviderError("provider unavailable")

    monkeypatch.setattr(freesound, "search_freesound_sounds", provider_failure)

    await VideoService._apply_sound_effects(
        clip,
        {"sfx_opportunities": [{"source_timestamp": "00:01", "query": "door"}]},
        (0.0, 2.0),
        [],
        "task-1",
        1,
    )

    payload = json.loads(clip.with_suffix(".sfx.json").read_text())
    assert payload["degraded"] is True
    assert payload["placements"] == []


def test_task_analysis_cache_key_changes_with_sfx_count_and_clamps_count():
    key0 = TaskService._build_cache_key(" url ", "upload", "fast", False, 0)
    key5 = TaskService._build_cache_key("url", "upload", "fast", False, 5)
    assert key0 != key5
    assert key5 == TaskService._build_cache_key("url", "upload", "fast", False, 99)


@pytest.mark.asyncio
async def test_freesound_download_enforces_streamed_byte_limit(monkeypatch, tmp_path):
    _StreamingClient.calls = []
    _StreamingClient.response = _StreamingResponse(
        blocks=[b"ID3", b"x" * freesound.FREESOUND_MAX_DOWNLOAD_BYTES]
    )
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _StreamingClient)

    with pytest.raises(freesound.FreesoundProviderError, match="size limit"):
        await freesound.download_freesound_mp3(_sound(), tmp_path / "download.mp3")

    assert not (tmp_path / "download.mp3").exists()


@pytest.mark.asyncio
async def test_freesound_download_uses_authorization_header_and_streams(monkeypatch, tmp_path):
    config = Config(); config.freesound_api_key = TOKEN; set_config_override(config)
    _StreamingClient.calls = []
    _StreamingClient.response = _StreamingResponse(blocks=[b"ID3", b"audio"])
    monkeypatch.setattr(freesound.httpx, "AsyncClient", _StreamingClient)
    try:
        result = await freesound.download_freesound_mp3(_sound(), tmp_path / "download.ogg")
    finally:
        set_config_override(None)

    assert result.read_bytes() == b"ID3audio"
    method_url, kwargs = _StreamingClient.calls[0]
    assert method_url == ("GET", "https://cdn.test/source.ogg")
    assert kwargs["headers"] == {"Authorization": f"Token {TOKEN}"}


def test_config_defaults_freesound_api_key_to_unset_without_environment(monkeypatch):
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "get_cached_setting", lambda _name: None)

    assert Config().freesound_api_key is None


def test_sound_effect_count_schema_lives_in_init_sql():
    repo_root = Path(__file__).parents[3]
    init_sql = (repo_root / "init.sql").read_text()

    tasks_schema = (
        "sound_effects_count INTEGER NOT NULL DEFAULT 0 "
        "CHECK (sound_effects_count >= 0 AND sound_effects_count <= 5)"
    )
    cache_schema = (
        "sound_effects_count INTEGER NOT NULL DEFAULT 0 "
        "CHECK (sound_effects_count >= 0 AND sound_effects_count <= 5)"
    )
    assert tasks_schema in init_sql
    assert cache_schema in init_sql

    # Sound schema is merged into init.sql; no dated sound migration files remain.
    migration_dir = repo_root / "backend" / "src" / "migrations" / "sql"
    assert not list(migration_dir.glob("20260817_*_sound_effects*.sql"))
    assert not list(migration_dir.glob("20260817_*_cache_sound_effects*.sql"))


def test_redaction_removes_freesound_token_from_provider_log_text(monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", TOKEN)

    redacted = redact_secrets(f"Freesound request failed with Token {TOKEN}")

    assert TOKEN not in redacted
    assert "[REDACTED]" in redacted
