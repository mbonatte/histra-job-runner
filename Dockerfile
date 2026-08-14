FROM python:3.12-slim

WORKDIR /opt/histra

# pip needs git to install the pinned histra-python VCS dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY . /src/histra-job-runner
RUN python -m pip install --no-cache-dir /src/histra-job-runner

RUN mkdir -p /work && chown -R 65532:65532 /work
USER 65532:65532
WORKDIR /work
ENTRYPOINT ["histra-runner"]
CMD ["worker"]
