"""Same generate interface for a disclosed fixture and an explicitly paid provider."""

import copy
import json
import logging
import os
from pathlib import Path
import re

import httpx

from common import AppError, read_json, sha256


def trust_proxy_env():
    """True only when the operator explicitly opts into env proxies.

    Default False: API calls (which carry the key) must not follow proxy
    env vars or ~/.netrc silently. Sandboxed eval harnesses that cannot
    reach api.meta.ai directly set META_TRUST_PROXY=1 for transport only.
    """
    return os.environ.get("META_TRUST_PROXY", "").strip() == "1"


class MockProvider:
    def __init__(self, response_path):
        self.response_path = Path(response_path)

    def generate(self, video_path, manifest, schema):
        fixture = read_json(self.response_path)
        if not isinstance(fixture, dict) or set(fixture) != {"steps"}:
            raise AppError("Mock fixture must be an object containing only steps.")
        document = {"schema_version": "1.0", "source": copy.deepcopy(manifest["source"]), "steps": fixture["steps"]}
        return document, {
            "provider": "mock", "mock": True, "model": None, "usage": None,
            "prompt_version": None,
            "fixture_sha256": sha256(self.response_path),
            "notice": "Hand-authored fixture response; no video/audio recognition or paid API call.",
        }


def has_refusal(value):
    if isinstance(value, dict):
        return value.get("type") == "refusal" or bool(value.get("refusal")) or any(has_refusal(v) for v in value.values())
    return isinstance(value, list) and any(has_refusal(v) for v in value)


def safe_response_metadata(body):
    """Allowlisted diagnostics survive failed/incomplete responses without raw bodies."""
    if not isinstance(body, dict):
        return {"response_status": "invalid_body"}
    status = body.get("status")
    result = {"response_status": status if status in ("completed", "incomplete", "failed", "queued", "in_progress", "cancelled") else "unknown"}
    response_id = body.get("id")
    if isinstance(response_id, str) and re.fullmatch(r"resp_[A-Za-z0-9_-]{1,190}", response_id):
        result["response_id"] = response_id
    incomplete = body.get("incomplete_details")
    if isinstance(incomplete, dict):
        reason = incomplete.get("reason")
        result["incomplete_details"] = {"reason": reason if reason in ("max_output_tokens", "content_filter") else "other"}
    usage = body.get("usage")
    if isinstance(usage, dict):
        saved = {k: usage[k] for k in ("input_tokens", "output_tokens") if type(usage.get(k)) is int and usage[k] >= 0}
        for section, key in (("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens")):
            values = usage.get(section)
            if isinstance(values, dict) and type(values.get(key)) is int and values[key] >= 0:
                saved[section] = {key: values[key]}
        result["usage"] = saved
    return result


def parse_meta_response(body):
    if not isinstance(body, dict) or body.get("status") != "completed":
        raise AppError("Meta response is not completed (incomplete/failed/missing status); no task exported.")
    if has_refusal(body):
        raise AppError("Meta response contains refusal; no task exported.")
    output = body.get("output")
    if not isinstance(output, list):
        raise AppError("Meta response has no output array.")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                if not isinstance(part.get("text"), str):
                    raise AppError("Meta output_text is not a string.")
                texts.append(part["text"])
    text = "".join(texts).strip()
    if not text:
        raise AppError("Meta completed without non-empty output_text.")
    try:
        document = json.loads(text)
    except ValueError:
        raise AppError("Meta output_text is not valid JSON.") from None
    usage = body.get("usage", {})
    if not isinstance(usage, dict):
        raise AppError("Meta usage is invalid.")
    saved_usage = {}
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            raise AppError("Meta usage is missing valid input/output token counts.")
        saved_usage[key] = value
    input_details = usage.get("input_tokens_details", {})
    if isinstance(input_details, dict) and "cached_tokens" in input_details:
        cached = input_details["cached_tokens"]
        if type(cached) is int and 0 <= cached <= saved_usage["input_tokens"]:
            saved_usage["input_tokens_details"] = {"cached_tokens": cached}
    details = usage.get("output_tokens_details", {})
    if isinstance(details, dict) and "reasoning_tokens" in details:
        value = details["reasoning_tokens"]
        if type(value) is int and value >= 0:
            saved_usage["output_tokens_details"] = {"reasoning_tokens": value}
    # Reasoning is already included in output_tokens; never add it again.
    return document, saved_usage


PROMPT_VERSIONS = ("v1", "v2", "v3", "v4", "v5", "v6", "v6-speech", "v7", "v8", "v9")


def sanitize_hint(task_hint):
    return " ".join(str(task_hint or "").split())[:200]


def build_desc_prompt(manifest):
    return (
        "Describe each attached keyframe image in order. For each image, return its kf id, "
        "its t= seconds, text_seen (every readable on-screen word, copied verbatim; empty "
        "array when no text is readable), and state (one sentence: what objects/controls "
        "are visible and their state). Do not guess unreadable words. "
        "Return schema_version=1.0 and copy this source unchanged: "
        + json.dumps(manifest["source"], ensure_ascii=False)
    )


V6_TOP_FIELDS = ("video_kind", "kind_reason", "kind_evidence", "proc_start_ms",
                 "proc_end_ms", "speech")


def remote_schema_for(schema, version):
    """Shape sent to strict json_schema: required must list every property key.

    v1-v5 keep the original shape (no v6 fields); v6+ get the full shape with
    completed required lists. Local validation always uses the file schema.
    """
    out = copy.deepcopy(schema)
    props = out["properties"]
    if version in ("v6", "v6-speech", "v7", "v8", "v9"):
        out["required"] = list(props)
        for step in [out["properties"]["steps"]["items"]]:
            step["required"] = list(step["properties"])
        speech = props["speech"]["properties"]
        out["properties"]["speech"]["required"] = list(speech)
        turns = speech["turns"]["items"]["properties"]
        speech["turns"]["items"]["required"] = list(turns)
    else:
        for field in V6_TOP_FIELDS:
            props.pop(field, None)
        out["properties"]["steps"]["items"]["properties"].pop("speech_refs", None)
    return out


def build_prompt(version, manifest, task_hint=""):
    if version not in PROMPT_VERSIONS:
        raise AppError("Unsupported prompt version.")
    prompt = ""
    if version == "v9":
        prompt += (
            "This clip is a magnified time window cut from a longer video to settle "
            "disputed boundaries. List ONLY the visible action transitions inside "
            "this window as steps; do not judge the whole task and do not invent "
            "steps outside the window. All millisecond times are relative to THIS "
            "clip (0 is the window start). Prefer splitting over merging: when two "
            "motions look similar, cut between them if anything visible changes. "
        )
    prompt += (
        "Read the attached ORIGINAL MP4, including its audio when present. Produce a human-reviewable "
        "operation sequence based only on visible/audible evidence. Do not claim SAM tracking. "
        "Each concept has exactly the five string fields object/action/target/pre/done. "
        "Use ordered non-overlapping integer millisecond intervals relative to the first decoded video PTS. "
        "Reference only supplied sample IDs whose sample_time_ms falls inside that step's [start_ms,end_ms). "
        "Use explicit uncertainty in the five strings when evidence is unclear. Do not invent steps. "
        "Use conservative visual part names when ambiguous (for example, small round part); "
        + ("do not assume a small round part is a nut. " if version in ("v2", "v3", "v4", "v5", "v6", "v6-speech", "v7", "v8", "v9")
           else "do not assume a washer is a nut. ")
        + "Time intervals are approximate observation windows, not proof of exact completion. "
        "The done field must state a visible completion criterion and say unverified when completion is not shown. "
        "Keep concept strings concise and group continuous motions into meaningful steps. "
    )
    if version in ("v2", "v3", "v4", "v5", "v6", "v6-speech", "v7", "v8", "v9"):
        prompt += (
            "Cut a new step whenever the gripper opens or closes, or an object leaves or arrives at a place; "
            "one grasp and one place each count as one step; entry and exit may merge into adjacent steps. "
            "For each step, cite the frame IDs you used in source_frame_refs and write one visible evidence "
            "sentence in evidence naming what is seen and at which cited frames (for example, which gripper "
            "moved, which object left the table, whether the gripper is open or closed). "
            "The done field must describe a state visible in the cited frames (for example, part resting on "
            "target with gripper open); never write tightening, locking, or securing unless the video shows "
            "a fastening action; say unverified when completion is not shown. "
            "Write done as only the end state visible on screen; do not add conditions that cannot be seen. "
        )
    if version in ("v3", "v7"):
        prompt += (
            "When quoting on-screen text, copy only words that appear verbatim on screen; "
            "if text is unclear write unreadable; never guess synonyms or paraphrases for UI labels. "
        )
    if version in ("v4", "v5", "v6", "v6-speech", "v7", "v8", "v9"):
        prompt += (
            "Each click and each visible state flip is its own step; never merge two actions "
            "that move in opposite directions (for example check then uncheck, open then close) "
            "into one step. "
        )
    if version in ("v5", "v6", "v6-speech", "v7", "v8", "v9"):
        prompt += (
            "Step boundaries must be exact multiples of 1000 ms (0, 1000, 2000, ...); "
            "never use values like 3601 or 6999. "
        )
    if version in ("v6", "v6-speech", "v7", "v8", "v9"):
        prompt += (
            "First decide video_kind: procedural (someone doing a task step by step), "
            "non_procedural (scenery, talking head, music, gameplay, anything without a task), "
            "or uncertain. Write kind_reason (one sentence) and kind_evidence (up to three "
            "things you see). If non_procedural, return steps as an empty array; never invent "
            "steps. If only part of the video shows the task, split only that part and report "
            "proc_start_ms and proc_end_ms; otherwise set them to 0 and the source duration. "
            "If uncertain, still split steps. "
        )
    if version in ("v6", "v7", "v8", "v9"):
        prompt += (
            "Ignore the audio track; always return speech with status none and an empty "
            "turns array, and leave speech_refs empty. "
        )
    if version == "v6-speech":
        prompt += (
            "Also listen to the embedded audio and always return a speech object: status present "
            "with turn-level turns (start_ms, end_ms, text) when speech is heard, or status none "
            "with an empty turns array for music, noise, or silence. Steps may cite turn indexes in speech_refs alongside "
            "frame evidence. Speech only names objects, states intent, or locates cuts; a step "
            "stands and done holds only on visible evidence; when speech conflicts with video, "
            "follow the video and write needs_review in evidence. "
        )
    hint = sanitize_hint(task_hint)
    if hint:
        prompt += f"Operator-provided task hint, naming help only, verify against the video: {hint}. "
        if version == "v7":
            prompt += ("Use hint words only when found on screen, copied verbatim; "
                       "never copy hint words into the answer unseen. ")
    if manifest.get("keyframes"):
        prompt += ("Full-resolution keyframe images follow the video, each labeled "
                   "with its kf id and t= seconds. Read on-screen text from these images, "
                   "not from the compressed video; cite the kf id and t= in evidence "
                   "for every quoted word. ")
    if version == "v8" and manifest.get("desc_table"):
        prompt += ("A frame description table from a first pass follows. Quote on-screen "
                   "text ONLY from this table, verbatim; if a word is not in the table, "
                   "write unreadable instead of guessing. Table: "
                   + json.dumps(manifest["desc_table"], ensure_ascii=False) + " ")
    prompt += (
        "Return schema_version=1.0 and copy this source unchanged: "
        + json.dumps(manifest["source"], ensure_ascii=False)
        + ". Available frame references (images are for local traceability; the attached MP4 is the model input): "
        + json.dumps([{k: f[k] for k in ("id", "sample_time_ms", "source_frame", "pts")} for f in manifest["frames"]])
    )
    return prompt


class MetaProvider:
    def __init__(self, model="muse-spark-1.2", confirm_paid=False, prompt_version="v6", task_hint="",
                 keyframes=False, keyframe_dir=None, temperature=None):
        if model not in ("muse-spark-1.2", "muse-spark-1.3"):
            raise AppError("Unsupported model.")
        if not confirm_paid:
            raise AppError("Meta requires explicit --confirm-paid-api; no paid call made.")
        if prompt_version not in PROMPT_VERSIONS:
            raise AppError("Unsupported prompt version.")
        self.model = model
        self.prompt_version = prompt_version
        self.task_hint = sanitize_hint(task_hint)
        self.keyframes = bool(keyframes)
        self.keyframe_dir = Path(keyframe_dir) if keyframe_dir else None
        self.temperature = temperature

    def _upload_keyframes(self, client, headers, manifest, diagnostics):
        if self.keyframe_dir is None or not manifest.get("keyframes"):
            raise AppError("Keyframes requested but none extracted.")
        out = []
        for kf in manifest["keyframes"]:
            path = self.keyframe_dir / kf["file"]
            if not path.is_file() or sha256(path) != kf["image_sha256"]:
                raise AppError("A keyframe image is missing or changed.")
            with path.open("rb") as image:
                upload = client.post("https://api.meta.ai/v1/files", headers=headers,
                                     data={"purpose": "user_data"},
                                     files={"file": (path.name, image, "image/jpeg")})
            if not upload.is_success:
                raise AppError(f"Meta keyframe upload HTTP {upload.status_code}; no response request made.")
            body = upload.json()
            file_id = body.get("id") if isinstance(body, dict) else None
            if not isinstance(file_id, str) or not file_id:
                raise AppError("Meta keyframe upload succeeded without a file id.")
            secs = round(kf["sample_time_ms"] / 1000, 2)
            out.append((file_id, f"Keyframe {kf['id']} at t={secs}s (full resolution, read text from here)."))
        diagnostics["keyframe_images"] = len(out)
        return out

    def describe(self, manifest, desc_schema):
        """First pass of (b): images only, no video. Returns (table, metadata)."""
        try:
            key = os.environ["META_API_KEY"]
        except KeyError:
            raise AppError("META_API_KEY is not configured; no paid call made.") from None
        if not key.strip():
            raise AppError("META_API_KEY is empty; no paid call made.")
        if self.keyframe_dir is None or not manifest.get("keyframes"):
            raise AppError("Describe requested but no keyframes extracted.")
        prompt = build_desc_prompt(manifest)
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        diagnostics = {"provider": "meta", "mock": False, "model": self.model,
                       "pass": "describe", "prompt_version": self.prompt_version}
        try:
            with httpx.Client(timeout=120, follow_redirects=False, trust_env=trust_proxy_env()) as client:
                headers = {"Authorization": f"Bearer {key}"}
                kf_ids = self._upload_keyframes(client, headers, manifest, diagnostics)
                content = [{"type": "input_text", "text": prompt}]
                for kf_id, label in kf_ids:
                    content.append({"type": "input_text", "text": label})
                    content.append({"type": "input_image", "file_id": kf_id})
                schema = copy.deepcopy(desc_schema)
                schema["required"] = list(schema["properties"])
                item = schema["properties"]["frames"]["items"]
                item["required"] = list(item["properties"])
                response = client.post("https://api.meta.ai/v1/responses", headers=headers, json={
                    "model": self.model, "input": [{"type": "message", "role": "user", "content": content}],
                    "text": {"format": {"type": "json_schema", "name": "frame_descriptions",
                                        "schema": schema, "strict": True}},
                    "max_output_tokens": 8000, "stream": False, "reasoning": {"effort": "low"},
                })
                diagnostics["response_http_status"] = response.status_code
                if not response.is_success:
                    raise AppError(f"Meta describe HTTP {response.status_code}; no second pass made.")
                response_body = response.json()
                diagnostics.update(safe_response_metadata(response_body))
                document, usage = parse_meta_response(response_body)
        except AppError as exc:
            raise AppError(str(exc), details=diagnostics) from None
        except httpx.TimeoutException:
            raise AppError("Meta describe timed out; billing/completion unknown. No automatic retry.",
                           details=diagnostics) from None
        except Exception:
            raise AppError("Meta describe failed; remote error details withheld. No automatic retry.",
                           details=diagnostics) from None
        finally:
            logging.disable(previous_logging)
        return document, {"provider": "meta", "mock": False, "model": self.model, "usage": usage,
                          "pass": "describe", "prompt_version": self.prompt_version,
                          "notice": "First pass: keyframe descriptions only; no steps yet."}

    def generate(self, video_path, manifest, schema):
        try:
            key = os.environ["META_API_KEY"]
        except KeyError:
            raise AppError("META_API_KEY is not configured; no paid call made.") from None
        if not key.strip():
            raise AppError("META_API_KEY is empty; no paid call made.")
        if sha256(video_path) != manifest["source"]["video_sha256"]:
            raise AppError("Original MP4 changed before upload.")
        prompt = build_prompt(self.prompt_version, manifest, self.task_hint)
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        diagnostics = {"provider": "meta", "mock": False, "model": self.model, "max_output_tokens": 8000, "reasoning_effort": "low",
                         "prompt_version": self.prompt_version, "task_hint": self.task_hint or None}
        try:
            # No retries or redirects. Upload the original bytes, preserving audio.
            with httpx.Client(timeout=120, follow_redirects=False, trust_env=trust_proxy_env()) as client:
                headers = {"Authorization": f"Bearer {key}"}
                with Path(video_path).open("rb") as video:
                    upload = client.post("https://api.meta.ai/v1/files", headers=headers,
                                         data={"purpose": "user_data"}, files={"file": ("source.mp4", video, "video/mp4")})
                diagnostics["upload_http_status"] = upload.status_code
                if not upload.is_success:
                    raise AppError(f"Meta upload HTTP {upload.status_code}; no response request made.")
                upload_body = upload.json()
                file_id = upload_body.get("id") if isinstance(upload_body, dict) else None
                if not isinstance(file_id, str) or not file_id:
                    raise AppError("Meta upload succeeded without a file id.")
                content = [{"type": "input_text", "text": prompt},
                           {"type": "input_file", "file_id": file_id}]
                kf_ids = []
                if self.keyframes:
                    kf_ids = self._upload_keyframes(client, headers, manifest, diagnostics)
                    for kf_id, label in kf_ids:
                        content.append({"type": "input_text", "text": label})
                        content.append({"type": "input_image", "file_id": kf_id})
                body = {
                    "model": self.model,
                    "input": [{"type": "message", "role": "user", "content": content}],
                    "text": {"format": {"type": "json_schema", "name": "operation_steps",
                                        "schema": remote_schema_for(schema, self.prompt_version), "strict": True}},
                    "max_output_tokens": 8000, "stream": False, "reasoning": {"effort": "low"},
                }
                if self.temperature is not None:
                    body["temperature"] = self.temperature
                response = client.post("https://api.meta.ai/v1/responses", headers=headers, json=body)
                diagnostics["response_http_status"] = response.status_code
                if not response.is_success and response.status_code == 400 and kf_ids:
                    # Mixed video+images rejected: fall back to images only, once.
                    diagnostics["mixed_400_fallback"] = True
                    images_only = [c for c in content if c.get("type") != "input_file"]
                    images_only.insert(1, {"type": "input_text",
                                           "text": "Video unavailable; use the t= timestamps on the images for time."})
                    body["input"][0]["content"] = images_only
                    response = client.post("https://api.meta.ai/v1/responses", headers=headers, json=body)
                    diagnostics["response_http_status"] = response.status_code
                if not response.is_success:
                    raise AppError(f"Meta responses HTTP {response.status_code}; no task exported.")
                response_body = response.json()
                diagnostics.update(safe_response_metadata(response_body))
                document, usage = parse_meta_response(response_body)
        except AppError as exc:
            raise AppError(str(exc), details=diagnostics) from None
        except httpx.TimeoutException:
            raise AppError("Meta request timed out; billing/completion unknown. No automatic retry.", details=diagnostics) from None
        except Exception:
            raise AppError("Meta request failed; remote error details withheld. No automatic retry.", details=diagnostics) from None
        finally:
            logging.disable(previous_logging)
        return document, {"provider": "meta", "mock": False, "model": self.model, "usage": usage,
                          "upload_http_status": upload.status_code, "response_http_status": response.status_code,
                          "response_status": response_body["status"],
                          "reasoning_effort": "low", "max_output_tokens": 8000,
                          "prompt_version": self.prompt_version, "task_hint": self.task_hint or None,
                          "notice": "Original MP4 uploaded with audio intact when present; human review required."}
