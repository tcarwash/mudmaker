FROM python:3.12-slim AS mudmaker-base
WORKDIR /opt/mudmaker
RUN pip install --no-cache-dir asyncssh==2.21.0 mcp==1.30.0 uvicorn==0.54.0 fastapi==0.115.12 \
    && groupadd --gid 10001 mudmaker \
    && useradd --uid 10001 --gid 10001 --no-create-home mudmaker \
    && mkdir /data /world \
    && chown mudmaker:mudmaker /data /world
COPY game_engine.py decision_runtime.py runtime.py content.py runtime_management.py mcp_support.py plugin_api.py player_web.py ./
COPY player_client ./player_client
RUN chmod 0644 /opt/mudmaker/*.py
USER 10001:10001
EXPOSE 2222 8001 8080
CMD ["python", "/opt/mudmaker/runtime.py"]

FROM mudmaker-base AS game
COPY --chown=10001:10001 world /world
