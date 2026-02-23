FROM python:3.11-slim

LABEL maintainer="Genomics AI Interpretation Pipeline"
LABEL description="Pediatric SNP extraction and AI interpretation pipeline"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY .agents/ ./.agents/

# Create data and output directories
RUN mkdir -p data output

# Expose Streamlit port
EXPOSE 8501

# Default: run the CLI pipeline
# Override with: docker-compose run app streamlit run src/app.py
CMD ["python", "src/main.py", "--help"]
