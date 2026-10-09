"""视频生成 Agent：图片 + 配音 + 字幕 → 动态小视频。"""
import uuid
import asyncio
from pathlib import Path
import edge_tts
from moviepy.editor import (
    ImageClip, AudioFileClip, CompositeVideoClip,
    concatenate_videoclips, TextClip,
)

OUTPUT_DIR = Path("data/media/videos")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR = Path("data/media/temp")
TEMP_DIR.mkdir(parents=True, exist_ok=True)


async def _tts(text: str, path: str):
    """文字转语音。"""
    communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
    await communicate.save(path)


def _make_clip(image_path: str, duration: float):
    """图片转视频片段。"""
    return ImageClip(image_path).set_duration(duration).resize((1080, 1440))


def generate_video(image_path: str, title: str, content: str) -> str:
    """图文 + 配音 → 视频。"""
    # 1. TTS 配音
    voice_path = str(TEMP_DIR / f"{uuid.uuid4().hex[:8]}.mp3")
    tts_text = f"{title}。{content}"
    asyncio.run(_tts(tts_text, voice_path))

    # 2. 音频时长决定图片展示时长
    audio = AudioFileClip(voice_path)
    duration = audio.duration

    # 3. 图片片段
    clip = _make_clip(image_path, duration)

    # 4. 加字幕
    txt_clip = TextClip(
        title, fontsize=48, color="white", font="Microsoft-YaHei",
        stroke_color="black", stroke_width=2,
    ).set_position(("center", "bottom")).set_duration(duration)

    # 5. 合成
    video = CompositeVideoClip([clip, txt_clip]).set_audio(audio)

    # 6. 导出
    output_path = str(OUTPUT_DIR / f"{uuid.uuid4().hex[:8]}.mp4")
    video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")

    return output_path


def video_generator_node(state: dict) -> dict:
    """Supervisor 调度入口。"""
    try:
        copy_result = state.get("copy_result") or {}
        image_result = state.get("image_result") or {}

        if not copy_result.get("content"):
            return {"status": "failed", "error": "没有文案，无法生成视频"}
        if not image_result.get("image_path"):
            return {"status": "failed", "error": "没有图片，无法生成视频"}

        video_path = generate_video(
            image_path=image_result["image_path"],
            title=copy_result.get("title", ""),
            content=copy_result.get("content", ""),
        )
        return {
            "video_result": {"video_path": video_path},
            "status": "done",
        }
    except Exception as e:
        return {"status": "failed", "error": f"视频生成失败：{e}"}