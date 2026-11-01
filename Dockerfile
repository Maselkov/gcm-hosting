FROM python:3.15-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY hosting ./hosting
# For deploy.sh, which takes the compose files from the release itself.
COPY deploy ./deploy
ENV DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "app.py"]
