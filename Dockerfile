FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY hosting ./hosting
ENV DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "app.py"]
