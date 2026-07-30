FROM python:3.12-slim
WORKDIR /opt/histra
COPY . /src/histra-job-runner
RUN python -m pip install --no-cache-dir /src/histra-job-runner
USER 65532:65532
ENTRYPOINT ["histra-runner"]
