FROM python:3.10-slim

# Install minimal system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    libgomp1 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy only the requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Use the PORT environment variable (default 8080 for Cloud Run)
ENV PORT=8080
EXPOSE 8080

# Run the application using uvicorn
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
