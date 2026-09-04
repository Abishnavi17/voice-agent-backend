# Dockerfile - the recipe to package THIS APP into a container image.
#
# A container image is a self-contained bundle: a specific OS base, the system
# libraries, the Python packages, and your code - everything needed to run,
# frozen so it behaves identically on any machine. `docker build` executes these
# steps top to bottom to produce that image; `docker run` (or compose) starts it.

# 1. Start from an official slim Python base. "slim" is small but complete.
FROM python:3.12-slim

# 2. Install FFmpeg (a SYSTEM package, not a pip one) because Whisper needs it to
#    decode audio. We clean the apt cache in the same layer to keep the image small.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 3. Set the working directory inside the container. Everything below happens here.
WORKDIR /code

# 4. Copy ONLY requirements first, then install. Docker caches layers, so as long
#    as requirements.txt does not change, the (slow) pip install is reused on
#    later builds even when your app code changes. A standard speed optimisation.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the application code into the image.
COPY app ./app

# 6. Document the port the app listens on (informational).
EXPOSE 8000

# 7. The command run when the container starts. host 0.0.0.0 makes the server
#    reachable from OUTSIDE the container (127.0.0.1 would only be internal).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
