# Cipherops pinned environment: one build graph for both ways of benchmarking.
#
# Targets (see docker/build.yml):
#   bench    the offline harness image: OpenSSL + liboqs + MTL, the C harness and the Python analysis code
#   nsd      authoritative server of the simulated lab
#   unbound  validating resolver of the simulated lab
#   tools    unbound + ldns signer + Python client of the simulated lab
#
# All four share the `toolchain` stage (OpenSSL, liboqs, Verisign MTL), so a stack builds that once. Mirrors the build
# of the supplied reference implementation (verisign/MTL docker/Dockerfile_pqc_base + Dockerfile_mtl_mode) with every
# upstream input pinned to an exact tag or commit so trials are repeatable.
#
# The pins are deliberately NOT defaulted here: they come from the stack profile in ecosystem/stacks/<profile>.env,
# so the offline image and the lab cannot drift apart. Each image records its pins as io.cipherops.* labels, which
# the dashboard's Setup uses to verify a built or pulled image against the stack profile.

ARG FEDORA_IMAGE=registry.fedoraproject.org/fedora-minimal:42

#######################################
# Shared: OpenSSL + liboqs + Verisign MTL reference library
#######################################
FROM ${FEDORA_IMAGE} AS toolchain

# Packages first, before any pin is declared, so changing the profile reuses this layer.
RUN dnf -y install gcc git perl make cmake libtool autoconf automake bc findutils pkgconf-pkg-config \
        libevent-devel expat-devel flex bison python3 \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf

WORKDIR /opt/src

# Each pin is declared right before the step that uses it: Docker keys the cache of every later step on the
# arguments declared so far, so this lets a profile change rebuild only what actually differs.
ARG OPENSSL_TAG
RUN test -n "${OPENSSL_TAG}" || { echo "OPENSSL_TAG must come from a stack profile (ecosystem/stacks/<profile>.env)"; exit 1; } \
    && git clone --depth 1 --branch ${OPENSSL_TAG} https://github.com/openssl/openssl.git \
    && cd openssl && ./config --prefix=/usr/local --libdir=lib64 \
    && make -j"$(nproc)" && make install_sw && ldconfig

ARG LIBOQS_TAG
RUN test -n "${LIBOQS_TAG}" || { echo "LIBOQS_TAG must come from a stack profile"; exit 1; } \
    && git clone --depth 1 --branch ${LIBOQS_TAG} https://github.com/open-quantum-safe/liboqs.git \
    && cd liboqs && mkdir build && cd build \
    && cmake -DBUILD_SHARED_LIBS=ON -DCMAKE_BUILD_TYPE=Release \
             -DCMAKE_INSTALL_PREFIX=/usr/local -DCMAKE_INSTALL_LIBDIR=lib64 \
             -DOPENSSL_ROOT_DIR=/usr/local .. \
    && make -j"$(nproc)" && make install && ldconfig

ARG MTL_REPO=https://github.com/verisign/MTL.git
ARG MTL_COMMIT
# Upstream configure.ac always forces "-g -O0" (debug default). The source is
# left untouched; optimisation is set at make time so MTL glue code is compiled
# like liboqs (Release). MTL_MAKE_CFLAGS=upstream reproduces the as-shipped build.
ARG MTL_MAKE_CFLAGS="-fPIC -Wall -Wextra -O2"
RUN test -n "${MTL_COMMIT}" || { echo "MTL_COMMIT must come from a stack profile"; exit 1; } \
    && git clone ${MTL_REPO} mtl && cd mtl && git checkout ${MTL_COMMIT} \
    && autoreconf --install \
    && ./configure --libdir=/usr/local/lib64 \
    && if [ "${MTL_MAKE_CFLAGS}" = "upstream" ]; then \
           make -j"$(nproc)" && grep -E '^CFLAGS =' src/Makefile | sed 's/^CFLAGS = //' > /opt/src/mtl_cflags.txt; \
       else \
           make -j"$(nproc)" CFLAGS="${MTL_MAKE_CFLAGS}" && echo "${MTL_MAKE_CFLAGS}" > /opt/src/mtl_cflags.txt; \
       fi \
    && make install && ldconfig && cat /opt/src/mtl_cflags.txt

#######################################
# Target bench: runtime + benchmark harness + Python analysis stack
#######################################
FROM ${FEDORA_IMAGE} AS bench

RUN dnf -y install gcc make bc findutils procps-ng util-linux python3 python3-pip nsd tcpdump \
        iproute iproute-tc \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf

ARG STACK
ARG OPENSSL_TAG
ARG LIBOQS_TAG
ARG MTL_COMMIT

COPY --from=toolchain /usr/local/include /usr/local/include
COPY --from=toolchain /usr/local/lib64 /usr/local/lib64
COPY --from=toolchain /usr/local/bin /usr/local/bin
# Keep the reference tree (tests, fixtures, example tools) for the preflight gate.
COPY --from=toolchain /opt/src/mtl /opt/mtl
COPY --from=toolchain /opt/src/mtl_cflags.txt /opt/mtl_cflags.txt
RUN ldconfig

ENV CIPHEROPS_STACK=${STACK} \
    CIPHEROPS_OPENSSL_TAG=${OPENSSL_TAG} \
    CIPHEROPS_LIBOQS_TAG=${LIBOQS_TAG} \
    CIPHEROPS_MTL_COMMIT=${MTL_COMMIT} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /work
COPY requirements.txt /work/requirements.txt
RUN pip3 install --no-cache-dir --retries 10 --timeout 60 -r /work/requirements.txt

COPY harness /work/harness
RUN make -C /work/harness

COPY pyproject.toml /work/pyproject.toml
COPY src /work/src
COPY configs /work/configs
COPY scripts /work/scripts
COPY tests /work/tests
RUN pip3 install --no-cache-dir --no-deps -e /work

ARG VCS_REF=unknown
ARG VCS_URL=
LABEL io.cipherops.role="bench" io.cipherops.stack="${STACK}" io.cipherops.openssl-tag="${OPENSSL_TAG}" \
      io.cipherops.liboqs-tag="${LIBOQS_TAG}" io.cipherops.mtl-commit="${MTL_COMMIT}" \
      org.opencontainers.image.source="${VCS_URL}" org.opencontainers.image.revision="${VCS_REF}"

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["python3 -m cipherops.runner --help"]

#######################################
# Simulated lab: ldns signer, NSD and Unbound with MTL mode, on the same toolchain
#######################################
FROM toolchain AS ldns-build
ARG LDNS_COMMIT
RUN test -n "${LDNS_COMMIT}" \
    && git clone https://github.com/Verisign/mtl-mode-ldns.git ldns && cd ldns && git checkout ${LDNS_COMMIT} \
    && cd src && libtoolize -ci && autoreconf -fi \
    && ./configure --prefix=/usr/local --with-examples --with-ssl=/usr/local --libdir=/usr/local/lib64 \
                   --enable-pqc-algo-fl-dsa --enable-pqc-algo-ml-dsa --enable-pqc-algo-slh-dsa-sha2 \
                   --enable-pqc-algo-slh-dsa-shake --enable-pqc-algo-mayo-1 --enable-pqc-algo-mayo-2 \
                   --enable-pqc-algo-snova --enable-pqc-algo-mtl \
    && make -j"$(nproc)" && make install DESTDIR=/out

FROM toolchain AS nsd-build
ARG NSD_COMMIT
RUN test -n "${NSD_COMMIT}" \
    && git clone https://github.com/verisign/mtl-mode-nsd.git nsd && cd nsd && git checkout ${NSD_COMMIT} \
    && cd src && touch zparser.y \
    && (cd simdzone && autoreconf -fi) \
    && libtoolize -ci && aclocal && autoconf && autoheader \
    && ./configure --prefix=/usr/local --enable-root-server --enable-draft-mtl \
    && make -j"$(nproc)" && make nsd-mem \
    && mkdir -p /out/usr/local/bin \
    && cp nsd nsd-checkconf nsd-checkzone nsd-control nsd-mem /out/usr/local/bin/

FROM toolchain AS unbound-build
ARG UNBOUND_COMMIT
RUN test -n "${UNBOUND_COMMIT}" \
    && git clone https://github.com/Verisign/mtl-mode-unbound.git unbound && cd unbound && git checkout ${UNBOUND_COMMIT} \
    && cd src && libtoolize -ci && autoreconf -fi \
    && ./configure --prefix=/usr/local --sbindir=/usr/local/bin --with-ssl=/usr/local \
                   --enable-pqc-algo-fl-dsa --enable-pqc-algo-ml-dsa --enable-pqc-algo-slh-dsa-sha2 \
                   --enable-pqc-algo-slh-dsa-shake --enable-pqc-algo-mayo-1 --enable-pqc-algo-mayo-2 \
                   --enable-pqc-algo-snova --enable-pqc-algo-mtl-mode \
    && make -j"$(nproc)" && make install DESTDIR=/out

FROM ${FEDORA_IMAGE} AS lab-runtime
RUN dnf -y install libevent expat iproute iproute-tc tcpdump procps-ng python3 \
    && dnf clean all
RUN echo "/usr/local/lib64" > /etc/ld.so.conf.d/crypto.conf \
    && echo "/usr/local/lib" > /etc/ld.so.conf.d/local.conf
COPY --from=toolchain /usr/local/lib64 /usr/local/lib64
COPY --from=toolchain /usr/local/bin/openssl /usr/local/bin/openssl
RUN ldconfig

#######################################
# Target nsd
#######################################
FROM lab-runtime AS nsd
ARG STACK NSD_COMMIT MTL_COMMIT LIBOQS_TAG VCS_REF=unknown VCS_URL=
COPY --from=nsd-build /out/usr/local /usr/local
RUN groupadd -r nsd && useradd -r -g nsd -d /var/nsd -s /sbin/nologin nsd \
    && mkdir -p /var/nsd /zones && chown nsd:nsd /var/nsd
ENV ECO_STACK=${STACK} ECO_NSD_COMMIT=${NSD_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} ECO_LIBOQS_TAG=${LIBOQS_TAG}
LABEL io.cipherops.role="nsd" io.cipherops.stack="${STACK}" io.cipherops.liboqs-tag="${LIBOQS_TAG}" \
      io.cipherops.mtl-commit="${MTL_COMMIT}" io.cipherops.nsd-commit="${NSD_COMMIT}" \
      org.opencontainers.image.source="${VCS_URL}" org.opencontainers.image.revision="${VCS_REF}"
ENTRYPOINT ["/bin/bash", "-c"]

#######################################
# Target unbound
#######################################
FROM lab-runtime AS unbound
ARG STACK UNBOUND_COMMIT MTL_COMMIT LIBOQS_TAG VCS_REF=unknown VCS_URL=
COPY --from=unbound-build /out/usr/local /usr/local
RUN ldconfig && groupadd -r unbound && useradd -r -g unbound -d /etc/unbound -s /sbin/nologin unbound
ENV ECO_STACK=${STACK} ECO_UNBOUND_COMMIT=${UNBOUND_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} ECO_LIBOQS_TAG=${LIBOQS_TAG}
LABEL io.cipherops.role="unbound" io.cipherops.stack="${STACK}" io.cipherops.liboqs-tag="${LIBOQS_TAG}" \
      io.cipherops.mtl-commit="${MTL_COMMIT}" io.cipherops.unbound-commit="${UNBOUND_COMMIT}" \
      org.opencontainers.image.source="${VCS_URL}" org.opencontainers.image.revision="${VCS_REF}"
ENTRYPOINT ["/bin/bash", "-c"]

#######################################
# Target tools: unbound + ldns signer + Python client
#######################################
FROM unbound AS tools
ARG STACK LDNS_COMMIT NSD_COMMIT UNBOUND_COMMIT MTL_COMMIT LIBOQS_TAG VCS_REF=unknown VCS_URL=
COPY --from=ldns-build /out/usr/local /usr/local
COPY --from=nsd-build /out/usr/local/bin/nsd-checkzone /usr/local/bin/nsd-checkzone
RUN ldconfig && dnf -y install python3-pip bind-utils && dnf clean all
COPY docker/lab-requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --retries 10 --timeout 60 -r /tmp/requirements.txt
ENV ECO_STACK=${STACK} ECO_LDNS_COMMIT=${LDNS_COMMIT} ECO_NSD_COMMIT=${NSD_COMMIT} \
    ECO_UNBOUND_COMMIT=${UNBOUND_COMMIT} ECO_MTL_COMMIT=${MTL_COMMIT} \
    PYTHONPATH=/eco/src:/work/src PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
LABEL io.cipherops.role="tools" io.cipherops.stack="${STACK}" io.cipherops.liboqs-tag="${LIBOQS_TAG}" \
      io.cipherops.mtl-commit="${MTL_COMMIT}" io.cipherops.ldns-commit="${LDNS_COMMIT}" \
      io.cipherops.nsd-commit="${NSD_COMMIT}" io.cipherops.unbound-commit="${UNBOUND_COMMIT}" \
      org.opencontainers.image.source="${VCS_URL}" org.opencontainers.image.revision="${VCS_REF}"
WORKDIR /eco
