FROM python:3.12-slim
RUN pip install --no-cache-dir webssh && useradd -r -u 10002 webssh
USER webssh
EXPOSE 8888
CMD ["wssh", "--address=0.0.0.0", "--port=8888"]
