FROM python:3.12-slim

WORKDIR /app

# Install dependencies before copying application code so Docker can cache this
# layer and skip reinstalling on every code change.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code.
# In development this directory is replaced by the bind mount defined in docker-compose.yml.
COPY app/ ./app/

# Expose the application port (informational; the actual publish is in docker-compose.yml).
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
