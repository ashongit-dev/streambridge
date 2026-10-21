FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:99 HOME=/data QT_X11_NO_MITSHM=1 LIBGL_ALWAYS_SOFTWARE=1
RUN apt-get update && apt-get install -y --no-install-recommends obs-studio xvfb xauth python3 python3-venv python3-pip fonts-dejavu-core ca-certificates libgl1-mesa-dri && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH=/opt/venv/bin:$PATH
COPY app ./app
COPY entrypoint.sh fetch_frontend.py configure.py ./
RUN python3 fetch_frontend.py
RUN chmod +x entrypoint.sh
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
EXPOSE 8080
ENTRYPOINT ["/app/entrypoint.sh"]
