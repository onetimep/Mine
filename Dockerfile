FROM python:3.9-slim
WORKDIR /app

# Install FFmpeg + dependencies
RUN apt-get update && apt-get install -y ffmpeg

COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# Explicit run command
CMD ["python", "main.py"]  # Critical for Koyeb!
