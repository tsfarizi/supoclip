"""
ClipMetadataService: per-clip description + hashtag generation.

Isolated from Transcript Intelligence (ai.py). Owns prompt versioning,
LLM agent, validation, and deterministic fallback. Inline sync, degraded
allowed: LLM failure never fails the task.
"""

import logging
import time
from typing import Any, Optional

from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

from ..clip_metadata import (
    CLIP_METADATA_VERSION,
    ClipMetadata,
    ClipMetadataBatch,
    extract_keyword_fallback_hashtags,
    normalize_hashtags,
    sanitize_description,
)
from ..config import Config, get_config
from ..runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

_clip_metadata_agent: Optional[Agent[None, ClipMetadataBatch]] = None
_clip_metadata_signature: Optional[tuple] = None

CLIP_METADATA_SYSTEM_PROMPT = """You are a viral short-form copywriter for TikTok/Reels/Shorts.

Your job is to write per-clip marketing copy grounded ONLY in the provided segment text, hook title, and key topics. Never invent facts.

OUTPUT CONTRACT:
- Return valid JSON only. No Markdown, no code fences, no prose outside JSON.
- Top-level keys: "clips" (array, one per input segment in same order), optional "batch_reasoning".
- Each clip must have: "clip_index" (0-based), "description" (80-300 chars, 1-2 sentences, curiosity gap, no hashtags inside, plain text), "hashtags" (3-8 lowercase tags WITHOUT '#' prefix, 3-15 chars each, only [a-z0-9_]), "language" (id|en), "platform" (use the requested platform).
- description: 1-2 sentences, CTR-optimized, grounded in segment text. Do not copy hook_title verbatim >80%.
- hashtags: grounded in segment text/key_topics. Include 1 broad, 2 niche, 1 topic-specific. Lowercase, no spaces, no emoji.

GROUNDING RULES:
- Use only segment text + hook_title + key_topics + summary.
- If language is "auto" or missing, detect from segment text (id vs en).
- Platform shapes tone: tiktok=casual punchy, reels=aspirational, shorts=direct, generic=neutral.
- Do not add facts not in text.

Example JSON:
{"clips": [{"clip_index":0,"description":"Kesalahan fatal founder saat pitching ke VC yang bikin valuasi anjlok 40%. Pelajari cara perbaikinya dalam 30 detik.","hashtags":["startup","venturecapital","pitching","foundertips","fundraising"],"language":"id","platform":"tiktok","prompt_version":"clip-metadata-v1","fallback":false}]}
"""


def _split_llm_name(model_name: str) -> tuple[str, str | None]:
    if ":" not in model_name:
        return model_name.strip().lower(), None
    provider, name = model_name.split(":", 1)
    return provider.strip().lower(), name.strip() or None


def _build_transcript_model(runtime_config: Config):
    from ..ai import _build_transcript_model as _build

    return _build(runtime_config)


def get_clip_metadata_agent() -> Agent[None, ClipMetadataBatch]:
    global _clip_metadata_agent, _clip_metadata_signature
    runtime_config = get_config()
    provider, _ = _split_llm_name(runtime_config.llm)
    signature = (
        runtime_config.llm,
        runtime_config.openai_api_key,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
        CLIP_METADATA_VERSION,
    )
    if _clip_metadata_agent is None or _clip_metadata_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        from ..ai import _get_missing_llm_key_error

        err = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if err:
            raise RuntimeError(err)
        # Reuse same model builder as ai.py
        model = _build_transcript_model(runtime_config)
        _clip_metadata_agent = Agent[None, ClipMetadataBatch](
            model=model,
            output_type=ClipMetadataBatch,
            system_prompt=CLIP_METADATA_SYSTEM_PROMPT,
            output_retries=2,
        )
        _clip_metadata_signature = signature
    return _clip_metadata_agent


def build_clip_metadata_prompt(
    segments: list[dict[str, Any]],
    key_topics: list[str] | None,
    summary: str | None,
    platform: str = "generic",
    language: str = "auto",
) -> str:
    topics = ", ".join(key_topics or []) or "(none)"
    summary_line = summary or "(none)"
    seg_lines = []
    for i, seg in enumerate(segments):
        seg_lines.append(
            f"[{i}] clip_index={i} hook_title={seg.get('hook_title') or '(none)'} | text: {(seg.get('text') or '')[:600]}"
        )
    seg_block = "\n".join(seg_lines)
    return f"""Generate per-clip description + hashtags.

Context:
- platform: {platform}
- language: {language}
- key_topics: {topics}
- summary: {summary_line}

Segments (one output per segment, same order):
{seg_block}

Return JSON only with "clips" array length {len(segments)}. Each entry must include clip_index, description, hashtags, language, platform.
"""


class ClipMetadataService:
    """Stateless service; instantiated per pipeline call."""

    def __init__(self, config: Config | None = None):
        self.config = config or get_config()

    async def generate_batch(
        self,
        segments: list[dict[str, Any]],
        key_topics: list[str] | None = None,
        summary: str | None = None,
        platform: str = "generic",
        language: str = "auto",
    ) -> tuple[list[ClipMetadata], bool, float]:
        """Generate batch; returns (metadatas, degraded, elapsed_seconds).

        Never raises for LLM failure: falls back to deterministic.
        """
        if not segments:
            return [], False, 0.0
        platform = platform if platform in ("tiktok", "reels", "shorts", "generic") else "generic"
        started = time.perf_counter()
        try:
            agent = get_clip_metadata_agent()
            prompt = build_clip_metadata_prompt(segments, key_topics, summary, platform, language)
            result = await agent.run(prompt)
            batch: ClipMetadataBatch = result.output
            # Validate order/length, fix if LLM misordered
            batch.clips.sort(key=lambda c: c.clip_index)
            if len(batch.clips) != len(segments):
                raise ValueError(f"expected {len(segments)} clips, got {len(batch.clips)}")
            # Normalize hashtags to '#tag' form and re-validate
            validated: list[ClipMetadata] = []
            for c in batch.clips:
                # Ensure normalized form
                c.hashtags = normalize_hashtags(c.hashtags)
                # Re-sanitize description
                sanitized = sanitize_description(c.description)
                if sanitized is None:
                    raise ValueError(f"invalid description for clip {c.clip_index}")
                c.description = sanitized
                c.platform = platform  # enforce requested platform
                c.prompt_version = CLIP_METADATA_VERSION
                c.fallback = False
                validated.append(c)
            elapsed = round(time.perf_counter() - started, 3)
            logger.info("Clip metadata batch ready: %d clips in %ss", len(validated), elapsed)
            return validated, False, elapsed
        except Exception as exc:
            elapsed = round(time.perf_counter() - started, 3)
            logger.warning("Clip metadata LLM degraded (%s), falling back: %s", type(exc).__name__, exc)
            fallback = self._fallback_batch(segments, key_topics, platform, language)
            return fallback, True, elapsed

    def _fallback_batch(
        self,
        segments: list[dict[str, Any]],
        key_topics: list[str] | None,
        platform: str,
        language: str,
    ) -> list[ClipMetadata]:
        out: list[ClipMetadata] = []
        for i, seg in enumerate(segments):
            text = seg.get("text") or ""
            hook = seg.get("hook_title")
            # Description fallback: trimmed text preview + hook
            raw_desc = (hook + ". " if hook else "") + text
            raw_desc = raw_desc.strip()
            if len(raw_desc) < 80:
                raw_desc = (raw_desc + " Saksikan insight lengkapnya dalam clip ini.").strip()
            if len(raw_desc) > 300:
                cut = raw_desc[:301].rfind(" ")
                raw_desc = (raw_desc[:cut] if cut > 40 else raw_desc[:300]).strip()
            # Ensure 80-300 via sanitize, else force
            sanitized = sanitize_description(raw_desc)
            if sanitized is None:
                sanitized = "Insight menarik yang sayang untuk dilewatkan. Tonton sampai habis untuk paham konteks lengkapnya dan bagikan ke teman."
            hashtags = extract_keyword_fallback_hashtags(text, hook, key_topics, limit=5)
            # Guarantee 3 tags
            if len(hashtags) < 3:
                hashtags = (hashtags + ["#viral", "#fyp", "#trending"])[:3]
                hashtags = normalize_hashtags(hashtags)
            lang = "id" if language == "auto" else language
            # Heuristic: if text has many english stopwords, keep en
            try:
                out.append(
                    ClipMetadata(
                        clip_index=i,
                        description=sanitized,
                        hashtags=hashtags,
                        language=lang,
                        platform=platform,
                        prompt_version=CLIP_METADATA_VERSION,
                        fallback=True,
                    )
                )
            except Exception:
                # Last resort: minimal valid
                out.append(
                    ClipMetadata(
                        clip_index=i,
                        description="Insight penting yang perlu kamu tahu. Tonton sampai selesai dan bagikan jika bermanfaat untuk orang lain.",
                        hashtags=["#viral", "#fyp", "#insight"],
                        language=lang,
                        platform=platform,
                        prompt_version=CLIP_METADATA_VERSION,
                        fallback=True,
                    )
                )
        return out

    async def generate_single(
        self,
        seg: dict[str, Any],
        key_topics: list[str] | None = None,
        summary: str | None = None,
        platform: str = "generic",
        language: str = "auto",
        clip_index: int = 0,
    ) -> tuple[ClipMetadata, bool, float]:
        batch, degraded, elapsed = await self.generate_batch(
            [seg], key_topics=key_topics, summary=summary, platform=platform, language=language
        )
        # Fix clip_index to requested
        if batch:
            batch[0].clip_index = clip_index
        return (batch[0] if batch else self._fallback_batch([seg], key_topics, platform, language)[0], degraded, elapsed)
