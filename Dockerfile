FROM nvidia/cuda:12.4.1-devel-ubuntu22.04 AS builder

ARG PROFANITY_TRON_REPO=https://github.com/CNDingSi/Profanity-tron.git
ARG PROFANITY_TRON_REF=6b2b8ab32d68f1acdf778184e96a9e9e7d7b3a0f

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        ca-certificates \
        ocl-icd-opencl-dev \
        opencl-headers \
        libcurl4-openssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone "${PROFANITY_TRON_REPO}" . \
    && git checkout "${PROFANITY_TRON_REF}"

RUN make LDFLAGS="-s -lOpenCL -lcurl -mcmodel=large" -j"$(nproc)"

FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04

ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        ocl-icd-libopencl1 \
        libcurl4 \
        ca-certificates \
        clinfo \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && mkdir -p /etc/OpenCL/vendors \
    && echo "libnvidia-opencl.so.1" > /etc/OpenCL/vendors/nvidia.icd

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY --from=builder /src/profanity.x64 /app/profanity.x64
RUN chmod +x /app/profanity.x64

COPY rp_handler.py .

CMD ["python3", "-u", "rp_handler.py"]
