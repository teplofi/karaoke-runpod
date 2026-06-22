# Karaoke forced-alignment on RunPod Serverless (Demucs + stable-ts, CUDA).
FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

ENV PYTHONUNBUFFERED=1 \
    WHISPER_MODEL=small \
    HF_HOME=/models \
    XDG_CACHE_HOME=/models \
    TORCH_HOME=/models/torch

WORKDIR /app

# ffmpeg нужен demucs/whisper для чтения mp3/m4a
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# torchaudio под torch из базового образа (cu121)
RUN pip install --no-cache-dir torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/cu121

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- запекаем веса моделей в образ → быстрый cold start ---
# Whisper small (faster-whisper)
RUN python -c "import faster_whisper; faster_whisper.WhisperModel('small', device='cpu', compute_type='int8')"
# Demucs htdemucs
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs')"

COPY karaoke_align.py handler.py ./

CMD ["python", "-u", "handler.py"]
