"""ElevenLabs speech in and speech out. Text chat works when the key is absent."""

import json
import os
import uuid
import urllib.error
import urllib.request

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


class VoiceError(RuntimeError):
    """The speech service refused the request or is not configured."""


def api_key() -> str | None:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    return key or None


def _request(url: str, data: bytes, content_type: str) -> bytes:
    key = api_key()
    if key is None:
        raise VoiceError("Set ELEVENLABS_API_KEY to use voice.")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"xi-api-key": key, "Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400]
        raise VoiceError(f"ElevenLabs returned {error.code}: {detail}") from error


def transcribe(audio: bytes, mime: str = "audio/webm") -> str:
    model = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v1")
    boundary = uuid.uuid4().hex
    filename = "speech.webm" if "webm" in mime else "speech.wav"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="model_id"\r\n\r\n{model}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n"
    ).encode()
    body = head + audio + f"\r\n--{boundary}--\r\n".encode()
    payload = json.loads(_request(STT_URL, body, f"multipart/form-data; boundary={boundary}"))
    text = payload.get("text") or ""
    if not text.strip():
        raise VoiceError("No speech was recognised.")
    return text.strip()


def synthesize(text: str) -> bytes:
    voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE)
    spoken = " ".join(text.split())
    if len(spoken) > 600:
        spoken = spoken[:600]
    body = json.dumps({"text": spoken, "model_id": "eleven_multilingual_v2"}).encode()
    return _request(TTS_URL.format(voice_id=voice), body, "application/json")
