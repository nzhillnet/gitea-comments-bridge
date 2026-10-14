# Use a small Python runtime
FROM python:3.12-alpine

# Create a working directory
WORKDIR /app

# Add a non-root user for security
RUN adduser -D bridgeuser

# Copy bridge code into container
COPY bridge.py .

# Install dependencies
RUN pip install --no-cache-dir flask requests markdown

# Switch to non-root user
USER bridgeuser

# Expose port the bridge will listen on
EXPOSE 8080

# Run the bridge app
CMD ["python", "bridge.py"]
