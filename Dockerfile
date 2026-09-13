FROM python:3.11-slim AS runtime

RUN groupadd --system recorder && useradd --system --gid recorder --home /var/lib/depth-recorder recorder
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN mkdir -p /var/lib/depth-recorder/data && chown -R recorder:recorder /var/lib/depth-recorder
USER recorder
VOLUME ["/var/lib/depth-recorder/data"]
ENTRYPOINT ["depth-recorder"]
CMD ["record", "--config", "/etc/depth-recorder/config.yaml"]
