FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .
EXPOSE 8080
CMD ["gunicorn", "-w", "4", "--threads", "12", "-b", "0.0.0.0:8080", "rk:app"]
