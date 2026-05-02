import tempfile
import os
import io
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel

app = FastAPI()

model_size = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
print(f"Loading Whisper model: {model_size} on CPU...")
# Set compute_type to int8 for decent CPU performance and memory footprint
model = WhisperModel(model_size, device="cpu", compute_type="int8")
print("Whisper model loaded successfully.")

@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model_name: str = Form(default="whisper-1", alias="model"),
    response_format: str = Form(default="verbose_json"),
    language: str = Form(default=None) # let it auto-detect or force 'en'
):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(tmp_path, word_timestamps=True, language=language if language else None)
        
        words = []
        transcript_text = ""
        seg_list = []
        
        for segment in segments:
            transcript_text += segment.text + " "
            seg_info = {
                "id": segment.id,
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            }
            if segment.words:
                seg_words = [{"word": w.word, "start": w.start, "end": w.end} for w in segment.words]
                words.extend(seg_words)
                seg_info["words"] = seg_words
            seg_list.append(seg_info)
            
        return JSONResponse(status_code=200, content={
            "text": transcript_text.strip(),
            "segments": seg_list,
            "words": words,
            "language": info.language
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000)
