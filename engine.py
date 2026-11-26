from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output"
DATA = ROOT / "data"
STORY_DIR = ROOT / "korku_motoru" / "stories"
W, H, FPS = 1080, 1920, 24


def log(msg: str):
    print(f"[KORKU MOTORU] {msg}", flush=True)


def run(cmd: list[str], **kwargs):
    subprocess.run(cmd, check=True, **kwargs)


def load_story(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(data.get("scenes", [])) != 45:
        raise ValueError("Hikâye tam olarak 45 sahne içermeli.")
    if data.get("duration_seconds") != 180:
        raise ValueError("Video süresi 180 saniye olmalı.")
    for i, scene in enumerate(data["scenes"], 1):
        if scene.get("n") != i or not scene.get("prompt") or not scene.get("narration"):
            raise ValueError(f"Sahne {i} eksik veya sıra numarası hatalı.")
    return data


def cloudflare_image(prompt: str, dest: Path, scene_no: int):
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()

    if not account or not token:
        raise RuntimeError(
            "CLOUDFLARE_ACCOUNT_ID ve CLOUDFLARE_API_TOKEN GitHub Secrets olarak gerekli."
        )

    model = os.getenv(
        "CF_IMAGE_MODEL",
        "@cf/black-forest-labs/flux-2-klein-4b",
    )

    style = (
        "Professional hand-drawn 2D cinematic horror animation still, "
        "vertical portrait composition, expressive illustrated character, "
        "clean slightly imperfect ink contours, painted shading, muted navy "
        "blue and violet palette, atmospheric shadows, no photorealism, no 3D, "
        "no anime, no text. Main character is BATU: same 24-year-old young man, "
        "short messy dark brown hair, hazel-brown eyes, charcoal gray sweatshirt, "
        "navy sweatpants. Same small bedroom: bed, bedside table on the left, "
        "black charging cable, small lamp, rainy window on the right, digital "
        "clock, door opposite bed. Maintain character and room continuity. "
        "Scene description: " + prompt
    )

    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{account}/ai/run/{model}"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "image/png",
    }

    # FLUX.2 Klein 4B requires multipart/form-data.
    # requests creates the multipart boundary automatically.
    multipart_fields = {
        "prompt": (None, style),
        "width": (None, "768"),
        "height": (None, "1365"),
    }

    response = requests.post(
        url,
        headers=headers,
        files=multipart_fields,
        timeout=180,
    )

    if not response.ok:
        raise RuntimeError(
            f"Cloudflare image API sahne {scene_no}: "
            f"HTTP {response.status_code} {response.text[:500]}"
        )

    content_type = response.headers.get("content-type", "").lower()

    if "application/json" in content_type:
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(
                f"Cloudflare image API hata sahne {scene_no}: {payload}"
            )

        import base64

        raw_image = payload.get("result", {}).get("image")
        if not raw_image:
            raise RuntimeError(
                f"Cloudflare yanıtında görsel bulunamadı. Sahne {scene_no}"
            )

        dest.write_bytes(base64.b64decode(raw_image))
    else:
        dest.write_bytes(response.content)

    try:
        with Image.open(dest) as image:
            image.verify()
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"Cloudflare geçerli bir görsel döndürmedi. Sahne {scene_no}: {exc}"
        ) from exc

    return dest


def placeholder_image(scene: dict, dest: Path):
    # Local smoke-test fallback only; never used in a production run.
    image = Image.new("RGB", (768, 1365), (12, 18, 32))
    draw = ImageDraw.Draw(image)
    draw.ellipse(
        (240, 260, 530, 550),
        fill=(90, 86, 105),
        outline=(190, 180, 190),
        width=5,
    )
    draw.rectangle(
        (180, 520, 590, 1120),
        fill=(62, 65, 78),
        outline=(180, 180, 190),
        width=5,
    )
    draw.rectangle(
        (500, 80, 700, 220),
        fill=(20, 30, 45),
        outline=(120, 150, 180),
        width=3,
    )
    draw.text((35, 30), f"SCENE {scene['n']:02d}", (230, 230, 240))
    draw.text((35, 1180), scene.get("screen", "")[:40], fill=(235, 235, 245))
    image.save(dest)


def generate_images(story: dict, test_mode=False):
    folder = OUT / "images"
    folder.mkdir(parents=True, exist_ok=True)

    todo = []
    for scene in story["scenes"]:
        image_path = folder / f"scene_{scene['n']:02d}.png"
        if image_path.exists() and image_path.stat().st_size > 1000:
            continue
        todo.append((scene, image_path))

    if test_mode:
        for scene, image_path in todo:
            placeholder_image(scene, image_path)
        return

    workers = max(1, min(int(os.getenv("IMAGE_WORKERS", "4")), 8))
    log(f"{len(todo)} sahne görseli üretilecek; eşzamanlı istek: {workers}")

    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                cloudflare_image,
                scene["prompt"],
                image_path,
                scene["n"],
            ): (scene, image_path)
            for scene, image_path in todo
        }

        for future in concurrent.futures.as_completed(futures):
            scene, image_path = futures[future]
            try:
                future.result()
                log(f"Görsel hazır: {scene['n']}/45")
            except Exception as exc:
                errors.append((scene["n"], str(exc)))

    if errors:
        details = "; ".join(
            f"{number}: {message}" for number, message in errors[:8]
        )
        raise RuntimeError("Görsel üretimi tamamlanamadı: " + details)


def add_screen_text(image_path: Path, text: str):
    if not text:
        return

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            28,
        )
    except Exception:
        font = ImageFont.load_default()

    box = (45, image.height - 205, image.width - 45, image.height - 65)
    draw.rounded_rectangle(
        box,
        radius=22,
        fill=(8, 12, 22),
        outline=(115, 135, 165),
        width=2,
    )
    draw.text(
        (70, image.height - 165),
        text[:90],
        font=font,
        fill=(245, 245, 250),
    )
    image.save(image_path)


def make_clip(image_path: Path, dest: Path, idx: int):
    direction = "in" if idx % 2 == 0 else "out"
    if direction == "in":
        zoom = "min(zoom+0.00055,1.052)"
    else:
        zoom = "if(eq(on,1),1.052,max(zoom-0.00055,1.0))"

    video_filter = (
        f"scale=1200:2134:force_original_aspect_ratio=increase,"
        f"crop=1200:2134,"
        f"zoompan=z='{zoom}':d={FPS * 4}:s={W}x{H}:fps={FPS},"
        "setsar=1,format=yuv420p"
    )

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            video_filter,
            "-t",
            "4",
            "-r",
            str(FPS),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "24",
            str(dest),
        ]
    )


def make_music(path: Path, duration=180, seed="video_001"):
    import numpy as np

    sample_rate = 22050
    count = sample_rate * duration
    t = np.arange(count, dtype=np.float32) / sample_rate

    seed_value = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed_value)
    frequencies = rng.choice(
        [43.65, 55.0, 65.41, 73.42, 82.41],
        size=4,
        replace=False,
    )

    music = np.zeros(count, dtype=np.float32)
    for i, frequency in enumerate(frequencies):
        phase = float(rng.random() * 6.28)
        rate = float(rng.uniform(0.015, 0.04))
        music += (0.10 / (i + 1)) * np.sin(
            2 * np.pi * frequency * t
            + phase
            + np.sin(2 * np.pi * rate * t) * 0.6
        )

    pulse = 0.55 + 0.45 * np.sin(2 * np.pi * 0.11 * t)
    noise = rng.normal(0, 1, count).astype(np.float32)
    kernel = np.ones(700, dtype=np.float32) / 700
    ambience = np.convolve(noise, kernel, mode="same")

    music = (music * pulse + ambience * 0.035).astype(np.float32)
    fade = int(sample_rate * 3)
    music[:fade] *= np.linspace(0, 1, fade)
    music[-fade:] *= np.linspace(1, 0, fade)
    music = np.clip(music, -0.45, 0.45)

    stereo = np.stack([music, music], axis=1)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes((stereo * 32767).astype("<i2").tobytes())


def make_sfx(path: Path, duration=180):
    sample_rate = 22050
    count = sample_rate * duration
    samples = [0.0] * count

    for second, frequency in [
        (4.2, 740),
        (16.1, 660),
        (48.2, 740),
        (52.1, 520),
        (84.2, 740),
        (96.2, 520),
        (140.1, 740),
        (164.2, 660),
        (176.1, 740),
    ]:
        start = int(second * sample_rate)
        length = int(0.16 * sample_rate)
        for j in range(length):
            if start + j < count:
                samples[start + j] += (
                    0.12
                    * math.sin(2 * math.pi * frequency * j / sample_rate)
                    * math.exp(-j / (sample_rate * 0.05))
                )

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(
            b"".join(
                int(max(-1, min(1, value)) * 32767).to_bytes(
                    2, "little", signed=True
                )
                for value in samples
            )
        )


async def make_voice(text: str, path: Path):
    import edge_tts

    voice = os.getenv("TTS_VOICE", "tr-TR-AhmetNeural")
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate="-3%",
        pitch="-5Hz",
    )
    await communicate.save(str(path))


def make_srt(story, path: Path):
    words = []
    for scene in story["scenes"]:
        words.extend(scene["narration"].split())

    chunks = [
        " ".join(words[i : i + 7])
        for i in range(0, len(words), 7)
    ]
    total = max(1, len(chunks))

    def timestamp(seconds):
        milliseconds = int(seconds * 1000)
        hours = milliseconds // 3600000
        milliseconds %= 3600000
        minutes = milliseconds // 60000
        milliseconds %= 60000
        secs = milliseconds // 1000
        milliseconds %= 1000
        return (
            f"{hours:02}:{minutes:02}:{secs:02},"
            f"{milliseconds:03}"
        )

    lines = []
    for i, chunk in enumerate(chunks):
        start = 180 * i / total
        end = 180 * (i + 1) / total
        lines += [
            str(i + 1),
            f"{timestamp(start)} --> {timestamp(end)}",
            chunk,
            "",
        ]

    path.write_text("\n".join(lines), encoding="utf-8")


def assemble(story, test_mode=False):
    clips = OUT / "clips"
    clips.mkdir(parents=True, exist_ok=True)

    for scene in story["scenes"]:
        image_path = OUT / "images" / f"scene_{scene['n']:02d}.png"

        if scene.get("screen") and not test_mode:
            display_image = (
                OUT / "images" / f"scene_{scene['n']:02d}_display.png"
            )
            if (
                not display_image.exists()
                or display_image.stat().st_mtime < image_path.stat().st_mtime
            ):
                shutil.copy2(image_path, display_image)
                add_screen_text(display_image, scene["screen"])
            image_for_clip = display_image
        else:
            image_for_clip = image_path

        destination = clips / f"clip_{scene['n']:02d}.mp4"
        if not destination.exists() or destination.stat().st_size < 1000:
            make_clip(image_for_clip, destination, scene["n"])

        log(f"Klip hazır: {scene['n']}/45")

    concat_file = OUT / "concat.txt"
    concat_file.write_text(
        "\n".join(
            f"file '{(clips / f'clip_{scene['n']:02d}.mp4').as_posix()}'"
            for scene in story["scenes"]
        ),
        encoding="utf-8",
    )

    silent_video = OUT / "silent.mp4"
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-c",
            "copy",
            str(silent_video),
        ]
    )

    music = OUT / "music_unique.wav"
    sfx = OUT / "sfx.wav"
    voice = OUT / "narration.mp3"

    make_music(music, 180, story["id"])
    make_sfx(sfx, 180)

    narration = " ".join(
        scene["narration"] for scene in story["scenes"]
    )
    asyncio.run(make_voice(narration, voice))
    make_srt(story, OUT / "captions.srt")

    final_video = OUT / "KORKU_MOTORU_BATU_VIDEO_001.mp4"

    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(silent_video),
            "-i",
            str(voice),
            "-i",
            str(music),
            "-i",
            str(sfx),
            "-filter_complex",
            "[1:a]apad=whole_dur=180,atrim=0:180,volume=1.0[n];"
            "[2:a]volume=0.18[m];"
            "[3:a]volume=0.35[s];"
            "[m][s]amix=inputs=2:duration=longest[bg];"
            "[n][bg]amix=inputs=2:duration=longest:"
            "dropout_transition=2[a]",
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-t",
            "180",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(final_video),
        ]
    )

    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "json",
            str(final_video),
        ],
        text=True,
    )

    metadata = json.loads(probe)
    duration = float(metadata["format"]["duration"])
    video_stream = next(
        (
            stream
            for stream in metadata["streams"]
            if stream.get("codec_type") == "video"
        ),
        metadata["streams"][0],
    )

    if abs(duration - 180) > 1.0:
        raise RuntimeError(
            f"Son video süresi 180 saniye değil: {duration}"
        )

    if (
        video_stream.get("width") != W
        or video_stream.get("height") != H
    ):
        raise RuntimeError(
            "Son video çözünürlük kontrolünden geçemedi."
        )

    log(
        f"Video kalite kontrolü başarılı: "
        f"{duration:.2f}s, {W}x{H}"
    )
    return final_video


def upload_youtube(video: Path, story: dict):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    client_id = os.getenv("YOUTUBE_CLIENT_ID", "")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "")
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("YouTube OAuth secrets eksik.")

    credentials = Credentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    credentials.refresh(Request())

    youtube = build("youtube", "v3", credentials=credentials)

    body = {
        "snippet": {
            "title": "Neden Hâlâ Uyanıksın? | 02:43'te Gelen Mesaj",
            "description": (
                story["description"]
                + "\n\n#Korku #Gizem #Shorts"
            ),
            "tags": [
                "Korku",
                "Gizem",
                "Korku Hikayesi",
                "Animasyon",
                "Shorts",
            ],
            "categoryId": "24",
        },
        "status": {
            "privacyStatus": os.getenv("YOUTUBE_PRIVACY", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            str(video),
            mimetype="video/mp4",
            resumable=True,
            chunksize=8 * 1024 * 1024,
        ),
    )

    response = None
    while response is None:
        _, response = request.next_chunk()

    log("YouTube yükleme tamamlandı: " + response.get("id", "id yok"))
    return response.get("id")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--story", default="video_001.json")
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)

    story = load_story(STORY_DIR / args.story)

    history_path = DATA / "history.json"
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        history = {"published": []}

    if not args.self_test and any(
        item.get("id") == story["id"]
        for item in history["published"]
    ):
        log("Bu bölüm daha önce yayınlanmış; çift yükleme engellendi.")
        return

    generate_images(story, args.self_test)
    final_video = assemble(story, args.self_test)

    if not args.self_test and not args.no_upload:
        video_id = upload_youtube(final_video, story)
        history["published"].append(
            {
                "id": story["id"],
                "video_id": video_id,
                "title": story["title"],
                "uploaded_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                ),
            }
        )
        history_path.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    log("İşlem başarıyla tamamlandı: " + str(final_video))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("HATA: " + str(exc))
        sys.exit(1)
