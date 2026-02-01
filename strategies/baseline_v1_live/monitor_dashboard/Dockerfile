# Dockerfile for Monitor Dashboard
FROM python:3.11-slim

WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy dashboard code
COPY . .

# Create state directory for volume mount
RUN mkdir -p /app/state

# Expose Dash port
EXPOSE 8050

# Run dashboard (headless mode for Docker)
CMD ["streamlit", "run", "app.py", "--server.port=8050", "--server.address=0.0.0.0", "--server.headless=true"]
