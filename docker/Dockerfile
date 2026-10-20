# Cipherops pinned benchmark environment.
#
# Mirrors the build used by the supplied reference implementation
# (verisign/MTL docker/Dockerfile_pqc_base + Dockerfile_mtl_mode) with every
# upstream input pinned to an exact tag or commit so trials are repeatable.

ARG FEDORA_IMAGE=registry.fedoraproject.org/fedora-minimal:42

#######################################
# Stage 1: OpenSSL + liboqs + Verisign MTL reference library
#######################################
FROM ${FEDORA_IMAGE} AS build

ARG OPENSSL_TAG=openssl-3.6.0
ARG LIBOQS_TAG=0.16.0
ARG MTL_REPO=https://github.com/verisign/MTL.git
ARG MTL_COMMIT=7cef0191da55048111d252a567b7614672424ed3
# Upstream configure.ac always forces "-g -O0" (debug default). The source is
# left untouched; optimisation is set at make time so MTL glue code is compiled
# like liboqs (Release). MTL_MAKE_CFLAGS=upstream reproduces the as-shipped build.
ARG MTL_MAKE_CFLAGS="-fPIC -Wall -Wextra -O2"

RUN dnf -y install gcc git perl make cmake libtool autoconf automake bc findutils \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf

WORKDIR /opt/src

RUN git clone --depth 1 --branch ${OPENSSL_TAG} https://github.com/openssl/openssl.git \
    && cd openssl && ./config --prefix=/usr/local --libdir=lib64 \
    && make -j"$(nproc)" && make install_sw && ldconfig

RUN git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs.git \
    && cd liboqs && mkdir build && cd build \
    && cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib64 \
             -DOPENSSL_ROOT_DIR=/usr/local .. \
    && make -j"$(nproc)" && make install && ldconfig

RUN git clone ${MTL_REPO} mtl && cd mtl && git checkout ${MTL_COMMIT} \
    && autoreconf --install \
    && ./configure --libdir=/usr/local/lib64 \
    && if [ "${MTL_MAKE_CFLAGS}" = "upstream" ]; then \
           make -j"$(nproc)" && grep -E '^CFLAGS =' src/Makefile | sed 's/^CFLAGS = //' > /opt/src/mtl_cflags.txt; \
       else \
           make -j"$(nproc)" CFLAGS="${MTL_MAKE_CFLAGS}" && echo "${MTL_MAKE_CFLAGS}" > /opt/src/mtl_cflags.txt; \
       fi \
    && make install && ldconfig && cat /opt/src/mtl_cflags.txt

#######################################
# Stage 2: runtime + benchmark harness + Python analysis stack
#######################################
FROM ${FEDORA_IMAGE} AS runtime

ARG OPENSSL_TAG=openssl-3.6.0
ARG LIBOQS_TAG=0.16.0
ARG MTL_COMMIT=7cef0191da55048111d252a567b7614672424ed3

RUN dnf -y install gcc make bc findutils procps-ng util-linux python3 python3-pip nsd tcpdump \
        iproute iproute-tc \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf

COPY --from=build /usr/local/include /usr/local/include
COPY --from=build /usr/local/lib64 /usr/local/lib64
COPY --from=build /usr/local/bin /usr/local/bin
# Keep the reference tree (tests, fixtures, example tools) for the preflight gate.
COPY --from=build /opt/src/mtl /opt/mtl
COPY --from=build /opt/src/mtl_cflags.txt /opt/mtl_cflags.txt
RUN ldconfig

ENV CIPHEROPS_OPENSSL_TAG=${OPENSSL_TAG} \
    CIPHEROPS_LIBOQS_TAG=${LIBOQS_TAG} \
    CIPHEROPS_MTL_COMMIT=${MTL_COMMIT} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
COPY requirements.txt /work/requirements.txt
RUN pip3 install --no-cache-dir -r /work/requirements.txt

COPY harness /work/harness
RUN make -C /work/harness

COPY pyproject.toml /work/pyproject.toml
COPY src /work/src
COPY configs /work/configs
COPY scripts /work/scripts
COPY tests /work/tests
RUN pip3 install --no-cache-dir --no-deps -e /work

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["python3 -m cipherops.runner --help"]
