# CrispASR GGUF server: Kokoro + Chatterbox backends, one binary.
# Vulkan build (no ROCm stack); models auto-download into the volume.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl mesa-vulkan-drivers libgomp1 \
 && rm -rf /var/lib/apt/lists/*
ARG CRISP_VER=v0.8.54
RUN curl -sfL -o /tmp/crisp.tar.gz \
      https://github.com/CrispStrobe/CrispASR/releases/download/${CRISP_VER}/crispasr-linux-x86_64-vulkan.tar.gz \
 && tar xzf /tmp/crisp.tar.gz -C /opt && rm /tmp/crisp.tar.gz
ENV PATH=/opt/crispasr-linux-x86_64-vulkan:$PATH
ENV HOME=/data
WORKDIR /data/.cache/crispasr
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["crispasr", "--server"]
